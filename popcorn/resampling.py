import numpy as np

from skimage.transform import resize
import SimpleITK as Sitk

def conversion_from_uint16_to_float32(image, min_value, max_value):
    """Converts 16 bit uint into 32 bit float using min max parameters (0 -> min, 65535 -> max)

    Args:
        image (numpy.ndarray): input 16bit image
        min_value (float):     min float value
        max_value (float):     max float value

    Returns:
        (numpy.ndarray): converted image (float32)
    """
    image = image.astype(np.float32)
    return (image/65535) * (max_value - min_value) - min_value


def conversion_from_float32_to_uint16(image, min_value, max_value):
    """Converts 32 bit float into 16 bit uint using min max parameters (min -> 0, max -> 65535)

    Args:
        image (numpy.ndarray): input 32bit image
        min_value (float):     min float value
        max_value (float):     max float value

    Returns:
        (numpy.ndarray): converted image (uint16)
    """
    image = (image - min_value) / (max_value - min_value) * 65535
    return image.astype(np.uint16)


def normalize_image(image):
    """normalizes an image (from the [image.min;image.max] to [0;1])

    Args:
        image (numpy.ndarray): input image

    Returns:
        (numpy.ndarray): normalized image
    """
    max_image = np.amax(image)
    min_image = np.amin(image)
    image = np.asarray(image, np.float32)

    image = (image - min_image)/(max_image - min_image)
    return image


def normalize_image_min_max(image, min_image, max_image):
    """normalizes an image (from [min_image;max_image] to [0;1])

    Args:
        image (numpy.ndarray): input image
        min_image (float):     input image manual min value (all values below will be set to 0)
        max_image (float):     input image manual max value (all values above will be set to 1)

    Returns:
        (numpy.ndarray): normalized image
    """
    image = np.asarray(image, np.float32)

    image = (image - min_image)/(max_image - min_image)
    return image


def bin_resize(image, bin_factor):
    """resizes the image depending on a bin_factor

    Args:
        image (numpy.ndarray): input image
        bin_factor (int): binning factor

    Returns:
        (numpy.ndarray): binned image
    """
    nb_slices, width, height = image.shape
    if bin_factor > 0:
        nb_slices = int(nb_slices/bin_factor)
        width = int(width/bin_factor)
        height = int(height/bin_factor)
        dim = (nb_slices, width, height)
        return resize(image, dim, preserve_range=True)
    else:
        raise Exception('bin_factor must be strictly positive')


def flip_along_z_axis(input_image):
    """flips an image along z axis

    Args:
        input_image (numpy.ndarray): input image

    Returns:
        (numpy.ndarray): flipped image
    """
    return np.copy(np.flip(np.flipud(input_image), axis=2))


def resize_image(moving_image, reference_image):
    """resizes volume based on reference image

    Args:
        moving_image (numpy.ndarray):   image to resize
        reference_image (numpy.ndarray: image to resize on

    Returns:
        (numpy.ndarray, numpy.ndarray): resized image and ref image
    """
    moving_image_itk = Sitk.GetImageFromArray(moving_image)
    reference_image_itk = Sitk.GetImageFromArray(reference_image)

    dimension = reference_image_itk.GetDimension()

    reference_physical_size = np.zeros(dimension)
    reference_physical_size[:] = [(sz - 1) * spc if sz * spc > mx else mx for sz, spc, mx in
                                  zip(reference_image_itk.GetSize(), reference_image_itk.GetSpacing(), reference_physical_size)]

    # Create the reference image with a zero origin, identity direction cosine matrix and dimension
    reference_origin = np.zeros(dimension)
    reference_direction = np.identity(dimension).flatten()
    reference_size = [reference_image.shape[2], reference_image.shape[1],
                      reference_image.shape[0]]  # Arbitrary sizes, smallest size that yields desired results.
    reference_spacing = [phys_sz / (sz - 1) for sz, phys_sz in zip(reference_size, reference_physical_size)]

    reference_image = Sitk.Image(reference_size, moving_image_itk.GetPixelIDValue())
    reference_image.SetOrigin(reference_origin)
    reference_image.SetSpacing(reference_spacing)
    reference_image.SetDirection(reference_direction)

    # Modify the transformation to align the centers of the original and reference image instead of their origins.
    centering_transform = Sitk.AffineTransform(dimension)
    centering_transform.Scale((moving_image.shape[2] / reference_image.shape[2], moving_image.shape[1] / reference_image.shape[1],
                               moving_image.shape[0] / reference_image.shape[0]))

    centering_transform.SetTranslation((0, 0, 1))
    moving_image_itk = Sitk.Resample(moving_image_itk, reference_image, centering_transform, Sitk.sitkLinear, 0.0)

    # Start of registration declaration
    moving_image = Sitk.GetArrayFromImage(moving_image_itk)
    reference_image = Sitk.GetArrayFromImage(reference_image_itk)
    return moving_image, reference_image
