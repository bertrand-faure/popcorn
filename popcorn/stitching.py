import os
import sys
import glob
import shutil
import time

from skimage import filters
import numpy as np

from popcorn.input_output import open_image, open_sequence, save_tif_image, open_cropped_sequence, save_tif_sequence, \
    open_cropped_image, create_list_of_files
from popcorn.spectral_imaging.registration import registration_computation, apply_itk_transformation
from popcorn.resampling import interpolate_two_images

# -- registration library --
import SimpleITK as Sitk

def stitch_multiple_folders_into_one(list_of_folders, output_folder, delta_z, look_for_best_slice=True, copy_mode=0,
                                     security_band_size=10, overlap_mode=0, band_average_size=0, flip=False):
    """Function that stitches different folders into a unique one.

    Notes:
        1. First and last folders are treated differently (correlation with other folders is looked only on one side).
        2. Every other slices are moved or copied (depending on copy mode) in the output_folder.
        3. For the rest of folders, the slices are either moved or copied (depending on copy mode).

    Args:
        list_of_folders (list[str]): list of input folders (expects images in each of those folders whatever the format)
        output_folder (str):         complete output total_path
        delta_z (int):               supposed z discrete displacement (number of slices)
        look_for_best_slice (bool):  False: we don't look for best matched slice between folders, True : we do
        copy_mode (int):             0: files are moved (no backup), 1: files are copied in the output_folder
        security_band_size (int):    nb of slices above and below delta_z used for stitching computation
        overlap_mode (int):          0: copy/move files, 1: standard average in band_average_size, 2: weighted average
        band_average_size (int):     If overlap_mode > 0: size of the band for the (weighted) average
        flip (bool):                 True: alphabetic filenames in a folder False: Reverse alphabetic order
    """
    list_of_folders.sort()
    number_of_folders = len(list_of_folders)
    folder_nb = 0
    bottom_overlap_index = 0
    top_overlap_index = 0

    # Parsing all input folders
    for folder_name in list_of_folders:
        print("Stitching step ", str(folder_nb) + "/", str(len(list_of_folders)))

        # We retrieve the list of filenames in the very first folder
        bottom_image_filenames = glob.glob(folder_name + '/*.tif') + glob.glob(folder_name + '/*.edf') \
                                 + glob.glob(folder_name + '/*.png')

        if flip:
            bottom_image_filenames.sort(reverse=True)
        else:
            bottom_image_filenames.sort()

        nb_slices = len(bottom_image_filenames)

        # We compute stitching on all folders (N folders implies N-1 stitching computations)
        if folder_nb < number_of_folders - 1:
            # We retrieve the list of filenames for the folder next to the current one
            top_image_filenames = glob.glob(list_of_folders[folder_nb + 1] + '/*.tif') \
                                  + glob.glob(list_of_folders[folder_nb + 1] + '/*.edf') \
                                  + glob.glob(list_of_folders[folder_nb + 1] + '/*.png')
            if flip:
                top_image_filenames.sort(reverse=True)
            else:
                top_image_filenames.sort()

            # We use delta_z to determine the theoretical overlapping slice index
            supposed_bottom_overlap_slice = nb_slices - int((nb_slices - delta_z) / 2)
            supposed_top_overlap_slice = int((nb_slices - delta_z) / 2)

            # We're computing stitching on a band (not only one image)
            if security_band_size > 0:
                # If we don't trust delta_z value
                if look_for_best_slice:
                    # We only keep the filenames of the bands used for stitching computation
                    bottom_band_filenames = \
                        bottom_image_filenames[supposed_bottom_overlap_slice - int(security_band_size):
                                               supposed_bottom_overlap_slice + int(security_band_size)]
                    top_band_filenames = \
                        top_image_filenames[supposed_top_overlap_slice - int(security_band_size):
                                            supposed_top_overlap_slice + int(security_band_size)]

                    # We load the corresponding bands
                    bottom_band_image = open_sequence(bottom_band_filenames)
                    top_band_image = open_sequence(top_band_filenames)

                    # Stitching computation. Returns the overlapping slices index between given bands
                    overlap_index = int(look_for_maximum_correlation_band(bottom_band_image, top_band_image, 10, True))

                    # We compute the difference between theoretical overlap index and real overlap index
                    overlap_index_difference = security_band_size - overlap_index
                # If we trust delta_z value, we set the difference between theory and practice to 0
                else:
                    overlap_index_difference = 0

                # We compute for overlap index for the current folder
                bottom_overlap_index = supposed_bottom_overlap_slice + overlap_index_difference

                # List of filenames from current folder we need to copy
                list_to_copy = bottom_image_filenames[top_overlap_index:bottom_overlap_index]

                # If we do not average images
                if overlap_mode == 0:
                    for slice_index in range(0, len(list_to_copy)):
                        # If the filenames are in reverse order
                        if flip == 1:
                            output_filename = output_folder + '/' + os.path.basename(list_to_copy[-(slice_index + 1)])
                        else:
                            output_filename = output_folder + '/' + os.path.basename(list_to_copy[slice_index])
                        # We either copy or move files depending on copy_mode
                        if copy_mode == 0:
                            os.rename(list_to_copy[slice_index], output_filename)
                        else:
                            shutil.copy2(list_to_copy[slice_index], output_filename)

                    # In case of no average, the overlapping index in the next folder is the supposed one
                    top_overlap_index = supposed_top_overlap_slice
                else:
                    for slice_index in range(0, len(list_to_copy)):
                        # If the filenames are in reverse order
                        if flip:
                            output_filename = output_folder + '/' + os.path.basename(list_to_copy[-(slice_index + 1)])
                        else:
                            output_filename = output_folder + '/' + os.path.basename(list_to_copy[slice_index])

                        # We either copy or move files depending on copy_mode
                        if copy_mode == 0:
                            os.rename(list_to_copy[slice_index], output_filename)
                        else:
                            shutil.copy2(list_to_copy[slice_index], output_filename)

                    # We retrieve the filenames used for averaging
                    bottom_average_filenames = \
                        bottom_image_filenames[bottom_overlap_index - int(band_average_size / 2):
                                               bottom_overlap_index + int(band_average_size / 2)]

                    top_average_filenames = \
                        top_image_filenames[supposed_top_overlap_slice +
                                            overlap_index_difference - int(band_average_size / 2):
                                            supposed_top_overlap_slice +
                                            overlap_index_difference + int(band_average_size / 2)]
                    # We compute the average between the two images depending on
                    averaged_image = average_images_from_filenames(bottom_average_filenames, top_average_filenames,
                                                                   overlap_mode)
                    # We save the averaged images
                    list_of_new_filenames = bottom_image_filenames[bottom_overlap_index - int(band_average_size / 2):
                                                                   bottom_overlap_index + int(band_average_size / 2)]

                    for filename in list_of_new_filenames:
                        output_filename = output_folder + os.path.basename(filename)
                        for i in range(0, band_average_size):
                            slice_data = averaged_image[i, :, :].squeeze()
                            save_tif_image(slice_data.astype(np.uint16), output_filename, bit=16)

                    # In case of no average, the overlapping index in the next folder is
                    # the supposed one + half of average band
                    top_overlap_index = supposed_top_overlap_slice + overlap_index_difference + int(
                        band_average_size / 2)

            # If the security_band_size is not > 0
            else:
                sys.exit("Please use a security_band_size > 0")
        # Once we computed stitching on all folders, we copy the remaining files (from the last folder)
        else:
            list_to_copy = bottom_image_filenames[top_overlap_index:-1]
            for slice_index in range(0, len(list_to_copy)):
                # If the filenames are in reverse order
                if flip:
                    output_filename = output_folder + '/' + os.path.basename(list_to_copy[-(slice_index + 1)])
                else:
                    output_filename = output_folder + '/' + os.path.basename(list_to_copy[slice_index])

                # We either copy or move files depending on copy_mode
                if copy_mode == 0:
                    os.rename(list_to_copy[slice_index], output_filename)
                else:
                    shutil.copy2(list_to_copy[slice_index], output_filename)
        print(" > corresponding slices found: slice", bottom_overlap_index, "and slice", top_overlap_index)
        folder_nb += 1


def average_images_from_filenames(first_image_filenames, second_image_filenames, mode=1):
    """Averages two images

    Args:
        first_image_filenames (list[str]):  list of first image filenames
        second_image_filenames (list[str]): list of second image filenames
        mode (int):                         1: standard average, 2: weighted average TODO

    Returns:
        numpy.ndarray: averaged image
    """
    # Opens image
    first_image = open_sequence(first_image_filenames)
    second_image = open_sequence(second_image_filenames)

    # If standard average requested
    if mode == 1:
        return (first_image + second_image) / 2
    # If weighted average requested
    else:
        return (first_image + second_image) / 2


def look_for_maximum_correlation(first_image, second_image):
    """Looks for the maximum correlated slice between two images

    The computation is only performed with the slice in the middle of first image and on the entire second image

    Args:
        first_image (numpy.ndarray):  first image
        second_image (numpy.ndarray): second image

    Returns:
        int: the slice number with highest zero normalized cross correlation.
    """
    first_nb_slices, first_width, first_height = first_image.shape
    second_nb_slices, second_width, second_height = second_image.shape

    width = max(first_width, second_width)
    height = max(first_height, second_height)

    middle_slice = int(first_nb_slices / 2)

    # We compute what we need for normalized cross correlation (first image middle slice)
    first_image_middle_slice = np.copy(first_image[middle_slice, :, :].squeeze())
    first_image_middle_slice = first_image_middle_slice - np.mean(first_image_middle_slice)
    first_image_middle_slice_std = np.std(first_image_middle_slice)

    # We compute what we need for normalized cross correlation (second image)
    centered_second_image = np.copy(second_image)
    for slice_nb in range(second_nb_slices):
        centered_second_image[slice_nb, :, :] = centered_second_image[slice_nb, :, :] \
                                                - np.mean(centered_second_image[slice_nb, :, :])
    centered_images_multiplication_result = first_image_middle_slice * centered_second_image

    # We compute normalized cross-correlation between first image middle slice and all second image slices
    normalized_cross_correlations = np.zeros(second_nb_slices)
    for slice_nb in range(0, second_nb_slices):
        second_image_slice_std = np.std(centered_second_image[slice_nb, :, :])
        sum_of_multiplied_images = np.sum(centered_images_multiplication_result[slice_nb, :, :])
        normalized_cross_correlation = sum_of_multiplied_images / (
                    first_image_middle_slice_std * second_image_slice_std)
        normalized_cross_correlation /= (width * height)
        normalized_cross_correlations[slice_nb] = normalized_cross_correlation  # array of normalized-cross correlations

    # The best candidate corresponds to the nb with max normalized cross-correlation
    best_corresponding_slice_nb = np.argmax(normalized_cross_correlations)

    return best_corresponding_slice_nb


def look_for_maximum_correlation_band(first_image, second_image, band_size, with_segmentation=True):
    """Looks for the maximum correlated slice between two images

    The computation is performed for every slices in a band of band_size centered around the first image middle slice

    Args:
        first_image (numpy.ndarray):  first image
        second_image (numpy.ndarray): second image
        band_size (int):              nb of slices above/below middle slice for computation
        with_segmentation (bool):     True: we perform thresholding, False: we don't

    Returns:
        int: the slice number with highest zero normalized cross correlation.
    """
    nb_slices, width, height = first_image.shape
    mask = np.zeros(first_image.shape)
    middle_slice_nb = int(nb_slices / 2)

    first_image_copy = np.copy(first_image)
    centered_second_image = np.copy(second_image)

    # If a thresholding is requested, we use Otsu thresholding on top 85% of the first image histogram
    if with_segmentation:
        thresh = filters.threshold_otsu(first_image_copy[first_image_copy > 0.15 * np.amax(first_image_copy)])
        if (first_image_copy[first_image_copy > thresh]).size / first_image_copy.size < 0.005:
            thresh = filters.threshold_otsu(first_image_copy)
        mask = first_image_copy > thresh
        first_image_copy = mask * first_image_copy
        centered_second_image = mask * centered_second_image

    # We compute what we need for normalized cross correlation (second image)
    for slice_nb in range(nb_slices):
        second_image_slice = centered_second_image[slice_nb, :, :]
        if with_segmentation:
            centered_second_image[slice_nb, :, :] = \
                mask[slice_nb, :, :] * (second_image_slice - np.mean(second_image_slice[second_image_slice > 0.0]))
        else:
            centered_second_image[slice_nb, :, :] = centered_second_image[slice_nb, :, :] \
                                                    - np.mean(centered_second_image[slice_nb, :, :])

    # We parse every slice of first_image[-band_size/2: band_size/2]
    best_slice_candidates = np.zeros(band_size)
    for i in range(int(-band_size / 2), (int(band_size / 2))):
        first_image_middle_slice = first_image_copy[middle_slice_nb + i, :, :].squeeze()
        # In case of thresholding, we use the computed mask on the current slice for computation
        if with_segmentation:
            first_image_middle_slice = \
                mask[middle_slice_nb + i, :, :] * \
                (first_image_middle_slice - np.mean(first_image_middle_slice[first_image_middle_slice > 0.0]))
        # In case of no thresholding, we don't use the mask for computation
        else:
            first_image_middle_slice = first_image_middle_slice - np.mean(first_image_middle_slice)
        first_image_middle_slice_std = np.std(first_image_middle_slice)
        normalized_cross_correlations = np.zeros(nb_slices)
        centered_images_multiplication_result = first_image_middle_slice * centered_second_image

        # We parse every slice of second image to compute normalized cross-correlations
        for slice_nb in range(nb_slices):
            centered_second_image_std = np.std(centered_second_image[slice_nb, :, :])
            sum_of_multiplied_images = np.sum(centered_images_multiplication_result[slice_nb, :, :])

            normalized_cross_correlation = \
                sum_of_multiplied_images / (first_image_middle_slice_std * centered_second_image_std)
            normalized_cross_correlation /= (width * height)
            normalized_cross_correlations[slice_nb] = normalized_cross_correlation  # arrays of normalized cross-corr

        # We store the best candidate for overlapping slice for each first image slice.
        best_corresponding_slice_nb = np.argmax(normalized_cross_correlations) - i
        best_slice_candidates[i + int(band_size / 2)] = best_corresponding_slice_nb

    # We finally retrieve the final best candidate (victory royale)
    computed_corresponding_slice_nb = np.median(best_slice_candidates)
    return computed_corresponding_slice_nb


def rearrange_folders_list(starting_position, number_of_lines, number_of_columns):
    """Sorts indices of multiple-tiles image based on starting position and size of the grid

    Args:
        starting_position (str): Position of first tile (either top-left, top-right, bottom-left or bottom-right)
        number_of_lines (int):   Number of lines in the final grid
        number_of_columns (int): Number of columns in the final grid

    Returns (list[int]): list of sorted indices

    """
    list_of_folders = list(range(number_of_lines * number_of_columns))
    for nb_line in range(number_of_lines):
        if (nb_line + ("left" in starting_position) * 1) % 2 == 0:
            list_of_folders[nb_line * number_of_columns: nb_line * number_of_columns + number_of_columns] = \
                list(reversed(
                    list_of_folders[nb_line * number_of_columns: nb_line * number_of_columns + number_of_columns]))

    if "bottom" in starting_position:
        new_list_of_folders = list(range(1, number_of_lines * number_of_columns + 1))
        for nb_line in range(number_of_lines):
            if nb_line * number_of_columns > 0:
                new_list_of_folders[nb_line * number_of_columns:
                                    nb_line * number_of_columns + number_of_columns] = \
                    list_of_folders[-(nb_line * number_of_columns + number_of_columns):
                                    -(nb_line * number_of_columns)]
            else:
                new_list_of_folders[nb_line * number_of_columns:
                                    nb_line * number_of_columns + number_of_columns] = \
                    list_of_folders[-(nb_line * number_of_columns + number_of_columns):]
    else:
        new_list_of_folders = list_of_folders
    return new_list_of_folders


def two_dimensions_stitching(ref_image, moving_image, supposed_offset=[0, 0]):
    """ Stitches 2 slices and combines them together
        supposed_offset : position of a pixel in ref_image - position of the same pixel in moving image
    Args:
        ref_image (numpy.ndarray):    reference image
        moving_image (numpy.ndarray): moving image
        supposed_offset (list[int]):  supposed offset between ref and moving image [x, y]

    Returns:
        (numpy.ndarray): combined images

    """
    initial_transform = Sitk.TranslationTransform(2)
    initial_transform.SetParameters((supposed_offset[0], supposed_offset[1]))

    moving_image_copy = apply_itk_transformation(moving_image, initial_transform)
    transformation = registration_computation(moving_image=moving_image_copy, ref_image=ref_image,
                                              transform_type="translation",
                                              metric="cc", verbose=False)

    transformation_parameters = transformation.GetParameters()
    if abs(supposed_offset[1]) > abs(supposed_offset[0]):
        transformation.SetParameters((transformation_parameters[0] + initial_transform.GetParameters()[0],
                                      (ref_image.shape[0] + transformation_parameters[1] + initial_transform.GetParameters()[1])))
    else:
        transformation.SetParameters(((ref_image.shape[1] + transformation_parameters[0] + initial_transform.GetParameters()[0]),
                                      transformation_parameters[1] + initial_transform.GetParameters()[1]))
    print("actual shift :", transformation.GetParameters())
    moving_image = apply_itk_transformation(moving_image, transformation)

    output_x_size = int(2*ref_image.shape[1] - moving_image.shape[1] + int(transformation_parameters[0])
                        + abs(initial_transform.GetParameters()[0]))
    output_y_size = int(2*ref_image.shape[0] - moving_image.shape[0] + int(transformation_parameters[1])
                        + abs(initial_transform.GetParameters()[1]))

    x_difference = output_x_size - ref_image.shape[1]
    y_difference = output_y_size - ref_image.shape[0]

    output_image = np.zeros((output_y_size, output_x_size))

    if abs(supposed_offset[1]) > abs(supposed_offset[0]) and supposed_offset[1] > 0:
        output_image[output_y_size - ref_image.shape[0]:output_y_size, :] = ref_image
        output_image[0:y_difference, :] = moving_image[0:y_difference, :]
        print("1")

    elif abs(supposed_offset[0]) < abs(supposed_offset[1]) and supposed_offset[1] < 0:
        print("2")
        output_image[0:ref_image.shape[0], :] = ref_image
        output_image[ref_image.shape[0]:output_y_size, :] = moving_image[0:y_difference, :]

    elif abs(supposed_offset[0]) > abs(supposed_offset[1]) and supposed_offset[0] > 0:
        print("3")
        output_image[:, output_x_size - ref_image.shape[1]:output_x_size] = ref_image
        output_image[:, 0:x_difference] = moving_image[:, 0:x_difference]

    elif abs(supposed_offset[1]) < abs(supposed_offset[0]) and supposed_offset[0] < 0:
        print("4")
        output_image[:, 0:ref_image.shape[1]] = ref_image
        output_image[:, ref_image.shape[1]:output_x_size] = moving_image[:, 0:x_difference]

    return output_image

def compute_two_tiles_registration(ref_image_input_folder, ref_image_coordinates, moving_image_input_folder,
                                   moving_image_coordinates):
    """ Computes and returns offset between overlap of two images

    Args:
        ref_image_input_folder (str):
        ref_image_coordinates (list[list[int]]):
        moving_image_input_folder (str):
        moving_image_coordinates (list[list[int]]):

    Returns:
        (Sitk.Transform): computed transformation

    """
    # We open the overlapping part of the ref image
    ref_image = open_cropped_sequence(glob.glob(ref_image_input_folder + "\\*"), ref_image_coordinates)

    # We compute the mask the registration will be based on (otsu threshold) -> faster registration
    threshold = filters.threshold_otsu(ref_image)
    ref_mask = np.copy(ref_image)
    ref_mask[ref_mask <= threshold] = 0
    ref_mask[ref_mask > threshold] = 1

    # We open the overlapping part of the moving image
    moving_image = open_cropped_sequence(glob.glob(moving_image_input_folder + "\\*"), moving_image_coordinates)
    moving_mask = np.copy(moving_image)

    # We compute the mask the registration will be based on (Otsu threshold) -> faster registration
    moving_mask[moving_mask <= threshold] = 0
    moving_mask[moving_mask > threshold] = 1

    # Registration computation (translation only)
    return registration_computation(moving_image=moving_image, ref_image=ref_image, ref_mask=ref_mask,
                                    moving_mask=moving_mask, transform_type="translation",
                                    metric="msq", verbose=False)


def multiple_tile_registration(input_folder, radix, starting_position="top-left", number_of_lines=4,
                               number_of_columns=3,
                               supposed_overlap=120, integer_values_for_offset=False, verbose=False):
    """Stitches multiple 3D tiles altogether and saves the result in a "final image" folder

    Args:
        input_folder (str):               Input tiles folder
        radix (str):                      Regex of input images
        starting_position (str):          Position of first tile (either top-left, top-right, bottom-left or bottom-right)
        number_of_lines (int):            Number of lines in the final grid
        number_of_columns (int):          Number of columns in the final grid
        supposed_overlap (int):           Theoretical overlap between each images
        integer_values_for_offset (bool): Integer values for registration ? (to avoid interpolation)

    Returns (None):

    """
    if verbose:
        full_time_start = time.clock()
        time_start = time.clock()
    # Sorts indices of input tiles so that every tile is registered from left to right & from top to bottom.
    folders_indices = rearrange_folders_list(starting_position, number_of_lines, number_of_columns)

    # Listing input folders
    list_of_folders = glob.glob(input_folder + radix + "*")
    reference_image_path = glob.glob(list_of_folders[0] + "\\" + "*")[0]

    # Opening a reference image for reference height/width
    reference_image = open_image(reference_image_path)
    nb_of_slices = len(glob.glob(list_of_folders[0] + "\\" + "*"))
    ref_height = reference_image.shape[0]
    ref_width = reference_image.shape[1]

    # Registration is computed on a subpart of each tile,
    # ref_image_coordinates correspond to the coordinates of the right sub-part of the left image
    # moving_image_coordinates correspond to the coordinates of the left sub-part of the right image
    ref_image_coordinates = [[0, nb_of_slices - 1],
                             [0, reference_image.shape[0] - 1],
                             [max(reference_image.shape[1] - supposed_overlap, 0), reference_image.shape[1] - 1]]

    moving_image_coordinates = [[0, nb_of_slices - 1],
                                [0, reference_image.shape[0] - 1],
                                [0, min(supposed_overlap, reference_image.shape[1] - 1)]]
    list_of_transformations = []
    if verbose:
        print("1. Introduction time:", (time.clock() - time_start))

    time_start = time.clock()
    # 1. Registration computation, for each line of the final grid, we compute the offset between each neighboring tiles
    i = 0
    for nb_line in range(number_of_lines):
        for nb_col in range(number_of_columns - 1):  # We compute registration on number_of_columns-1 pairs of images

            image_number = nb_line * number_of_columns + nb_col  # We keep track of the ref_image number we're working on
            print("stitching tile number", folders_indices[image_number],
                  "and tile number", folders_indices[image_number + 1])

            reg_start = time.clock()

            # transformation = compute_two_tiles_registration(list_of_folders[folders_indices[image_number]],
            #                                                 ref_image_coordinates,
            #                                                 list_of_folders[folders_indices[image_number + 1]],
            #                                                 moving_image_coordinates)
            import SimpleITK as Sitk
            transformation = Sitk.TranslationTransform(3)
            manual_transforms = [[0.126,-2.12,3.12],
                                [0.126,2.12,3.12],
                                [0.126,2.12,-3.12],
                                [0.126,-2.12,-3.12],
                                [0.126,-2.12,3.12],
                                [0.126,-2.12,3.12],
                                [0.126,-2.12,3.12],
                                [0.126,-2.12,3.12]]
            transformation.SetOffset((manual_transforms[i][0],manual_transforms[i][1],manual_transforms[i][2]))
            i+=1
            print("Registration time:", (time.clock() - reg_start))
            # If we want to avoid interpolation -> integer offset
            if integer_values_for_offset:
                transformation.SetOffset((round(transformation.GetOffset()[0]),
                                          round(transformation.GetOffset()[1]),
                                          round(transformation.GetOffset()[2])))

            list_of_transformations.append(transformation)
    if verbose:
        print("2. First Registrations time:", (time.clock() - time_start))
        time_start = time.clock()

    # 2. Concatenation of same line tiles, line are saved in combined_line_XX folders
    for nb_line in range(number_of_lines):
        x_position = 0
        list_of_z_offset = [0]
        for transformation_nb in range(number_of_columns - 1):  # Each tile needs to be registered using previous registrations
            print("transfo nb", transformation_nb)
            transformation_offset = list_of_transformations[nb_line * (number_of_columns - 1)
                                                             + transformation_nb].GetOffset()[-1].GetOffset()
            list_of_z_offset.append(transformation_offset[-1])
            list_of_transformations[nb_line * (number_of_columns - 1)
                                    + transformation_nb].GetOffset()[-1].SetOffset((transformation_offset[0],
                                                                                    transformation_offset[1],
                                                                                    0  ))
            list_of_z_offset[-1] = sum(list_of_z_offset)
        min_offset = min(list_of_z_offset)
        list_of_z_offset = [offset - min_offset for offset in list_of_z_offset]

        print("list of z offset:", list_of_z_offset)
        for slice_nb in range(nb_of_slices + round(max(list_of_z_offset))):
            list_of_slices = [[None, None], [None, None], [None, None]]
            # Creating the output image
            empty_slice = np.zeros((ref_height,
                                    ref_width * number_of_columns - (number_of_columns - 1) * supposed_overlap))

            # We add each tile on after the other
            for nb_col in range(number_of_columns):
                image_number = nb_line * number_of_columns + nb_col
                current_slice_nb = slice_nb - list_of_z_offset[nb_col]
                if current_slice_nb > 0:
                    if nb_col == 0:
                        if list_of_slices[nb_col][1] is not None:
                            list_of_slices[nb_col][1] = list_of_slices[nb_col][0]
                        else:
                            list_of_slices[nb_col][1] = open_cropped_image(glob.glob(list_of_folders[folders_indices[image_number]] + "\\*")[int(current_slice_nb)],
                                                                           [[0, -1], [0, ref_width - supposed_overlap // 2]])
                        list_of_slices[nb_col][0] = open_cropped_image(glob.glob(list_of_folders[folders_indices[image_number]] + "\\*")[int(current_slice_nb) + 1],
                                                                       [[0, -1], [0, ref_width - supposed_overlap // 2]])
                        current_slice = interpolate_two_images(list_of_slices[nb_col][1], list_of_slices[nb_col][0], current_slice_nb % 1)
                        empty_slice[:, :, 0:ref_width - supposed_overlap // 2 + 1] = current_slice
                        x_position = ref_width - supposed_overlap // 2
                    else:
                        if list_of_slices[nb_col][1] is not None:
                            list_of_slices[nb_col][1] = list_of_slices[nb_col][0]
                        else:
                            list_of_slices[nb_col][1] = open_image(glob.glob(list_of_folders[folders_indices[image_number]] + "\\*")[int(current_slice_nb)])
                        list_of_slices[nb_col][0] = open_image(glob.glob(list_of_folders[folders_indices[image_number]] + "\\*")[int(current_slice_nb) + 1])
                        current_slice = interpolate_two_images(list_of_slices[nb_col][1], list_of_slices[nb_col][0], current_slice_nb % 1)
                        x_position = ref_width - supposed_overlap // 2
        # empty_image = np.zeros((nb_of_slices,
        #                         ref_height,
        #                         ref_width * number_of_columns - (number_of_columns - 1) * supposed_overlap))
        # # We add each tile on after the other
        # for nb_col in range(number_of_columns):
        #     image_number = nb_line * number_of_columns + nb_col
        #
        #     # The first tile doesn't need any registration
        #     if nb_col == 0:
        #         empty_image[:, :, 0:ref_width - supposed_overlap // 2 + 1] = \
        #             open_cropped_sequence(glob.glob(list_of_folders[folders_indices[image_number]] + "\\*"),
        #                                   [[0, -1], [0, -1], [0, ref_width - supposed_overlap // 2]])
        #         x_position = ref_width - supposed_overlap // 2
        #
        #     # The next tile need to be registered/cropped
        #     else:
        #         image_to_register = open_sequence(glob.glob(list_of_folders[folders_indices[image_number]] + "\\*"))
        #         for transformation_nb in range(nb_col):  # Each tile needs to be registered using previous registrations
        #             image_to_register = apply_itk_transformation(image_to_register,
        #                                                          list_of_transformations[
        #                                                              nb_line * (number_of_columns - 1)
        #                                                              + transformation_nb])
        #         # After registration comes the cropping part (based on initial supposed overlap)
        #         image_to_register = \
        #             image_to_register[:, :, supposed_overlap - supposed_overlap // 2:ref_width - supposed_overlap // 2]
        #
        #         # We add the current tile to the final image
        #         empty_image[:, :, x_position: x_position + image_to_register.shape[2]] = image_to_register
        #
        #         # We update the "x_position" corresponding to the position the next tile needs to be positionned at
        #         x_position += image_to_register.shape[2] - 1
        #
        # print("-> Saving line number", nb_line, "in folder", input_folder + "combined_line_" + str(nb_line) + "\\")
        # save_tif_sequence(empty_image, input_folder + "combined_line_" + str(nb_line) + "\\")
        # print("--> Line number", nb_line, "saved !")

    # 3. Registration computation, we compute the offset between each neighboring lines
    list_of_transformations = []
    for nb_line in range(number_of_lines - 1):
        # This time, the registration is vertical, not horizontal. The overlapping parts correspond to the
        # bottom/upper-end of each line
        transformation = compute_two_tiles_registration(input_folder + "combined_line_" + str(nb_line),
                                                        [[0, -1], [-supposed_overlap, -1], [0, -1]],
                                                        input_folder + "combined_line_" + str(nb_line + 1),
                                                        [[0, -1], [0, supposed_overlap], [0, -1]])
        # If we want to avoid interpolation -> integer offset
        if integer_values_for_offset:
            transformation.SetOffset((round(transformation.GetOffset()[0]),
                                      round(transformation.GetOffset()[1]),
                                      round(transformation.GetOffset()[2])))
        list_of_transformations.append(transformation)

    # 4. Registration of each line (Because of memory limitations)
    for nb_line in range(number_of_lines - 1):
        line = open_sequence(input_folder + "combined_line_" + str(nb_line + 1) + "\\")
        for nb_transformation in range(nb_line + 1):
            line = apply_itk_transformation(line, list_of_transformations[nb_transformation])
        save_tif_sequence(line, input_folder + "registered_line_" + str(nb_line) + "\\")
        print("Registered line number", nb_line, "saved !")

    if verbose :
        print("3. Second Registrations time:", (time.clock() - time_start))
        time_start = time.clock()
    # 5. Concatenation of every lines of the final grid
    list_of_line_images = []
    list_of_len = []

    # For this purpose, we need to list all input slices
    for nb_line in range(number_of_lines):
        if nb_line == 0:
            list_of_line_images.append(create_list_of_files(input_folder + "combined_line_" + str(nb_line) + "\\",
                                                            "tif"))
        else:
            list_of_line_images.append(create_list_of_files(input_folder + "registered_line_" + str(nb_line - 1) + "\\",
                                                            "tif"))
        list_of_len.append(len(list_of_line_images[-1]))

    # We concatenate lines one slice at a time
    empty_image = np.zeros((number_of_lines * ref_height - supposed_overlap * (number_of_lines - 1),
                            ref_width * number_of_columns - supposed_overlap * (number_of_columns - 1)))
    # Concatenation
    for nb_image in range(min(list_of_len)):
        for nb_line in range(number_of_lines):
            out_slice = open_image(list_of_line_images[nb_line][nb_image])
            if nb_line == 0:
                empty_image[0: out_slice.shape[0] - supposed_overlap // 2, :] = \
                    out_slice[0:out_slice.shape[0] - supposed_overlap // 2, :]
            else:
                empty_image[nb_line * (ref_height - supposed_overlap // 2) - (nb_line - 1) * supposed_overlap // 2:
                            nb_line * ref_height - (2 * nb_line + 1) * supposed_overlap // 2 + out_slice.shape[0], :] \
                    = out_slice[supposed_overlap // 2:out_slice.shape[0] - supposed_overlap // 2, :]
        save_tif_image(empty_image, input_folder + "final_image\\" + '{:04d}'.format(nb_image))
    print("Stitching done.")

    if verbose:
        print("4. Second Concatenation time:", (time.clock() - time_start))
        print("Total:", (time.clock() - full_time_start))


if __name__ == "__main__":
    input_folder = "C:\\Users\\ctavakol\\Desktop\\2d_stitching\\"
    ref_image = open_image(input_folder + "left_image.tif")
    moving_image = open_image(input_folder + "right_image.tif")

    save_tif_image(two_dimensions_stitching(ref_image, moving_image, [-440, 0]), input_folder + "output_image")
