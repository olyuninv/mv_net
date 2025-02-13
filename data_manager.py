#
# KTH Royal Institute of Technology
#

# IMPLEMENTATION BASED ON CODE:
# https://github.com/martkartasev/sepconv
#
# MIT License
#
# Copyright (c) 2018
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.


import json
import random
import zipfile
import numpy as np
import cv2 as cv
import re
from joblib import Parallel, delayed
from timeit import default_timer as timer
from torchvision.transforms.functional import crop as crop_image
from os.path import exists, join, basename, isdir
from os import makedirs, remove, listdir, rmdir, rename
from six.moves import urllib
from PIL import Image

import src.config as config


############################################# UTILITIES #############################################

def load_img(file_path):
    """
    Reads an image from disk.
    :param file_path: Path to the image file
    :return: PIL.Image object
    """
    return Image.open(file_path).convert('RGB')


def is_image(file_path):
    """
    Checks if the specified file is an image
    :param file_path: Path to the candidate file
    :return: Whether or not the file is an image
    """
    return any(file_path.endswith(extension) for extension in [".png", ".jpg", ".jpeg"])


def load_tuples(root_path, stride, tuple_size, paths_only=True):
    """
    Reads the content of a directory coupling the files together in tuples.
    :param root_path: Path to the directory
    :param stride: Number of steps from one tuple to the next
    :param tuple_size: Size of each tuple
    :param paths_only: If true, the tuples will contain paths rather than PIL.Image objects
    :return: List of tuples containing the images or their paths
    """

    frames = [join(root_path, x) for x in listdir(root_path)]
    frames = [x for x in frames if is_image(x)]
    frames.sort()

    if not paths_only:
        frames = [load_img(x) for x in frames]

    tuples = []
    for i in range(1 + (len(frames) - tuple_size) // stride):
        tuples.append(tuple(frames[i * stride + j] for j in range(tuple_size)))

    return tuples


def load_patch(patch):
    """
    Reads the three images of a patch from disk and returns them already cropped.
    :param patch: Dictionary containing the details of the patch
    :return: Tuple of PIL.Image objects corresponding to the patch
    """
    paths = (patch['left_frame'], patch['middle_frame'], patch['right_frame'])
    i, j = (patch['patch_i'], patch['patch_j'])
    imgs = [load_img(x) for x in paths]
    h, w = config.PATCH_SIZE
    return tuple(crop_image(x, i, j, h, w) for x in imgs)


def load_cached_patch(cached_patch):
    """
    Reads the three cached images of a patch from disk. Can only be used if the patches
    have been previously cached.
    :param cached_patch: Patch as a tuple (path_to_left, path_to_middle, path_to_right)
    :return: Tuple of PIL.Image objects corresponding to the patch
    """
    return tuple(load_img(x) for x in cached_patch)

############################################### DAVIS ###############################################

def get_davis_16(dataset_dir):
    return _get_davis(dataset_dir, "DAVIS", "https://graphics.ethz.ch/Downloads/Data/Davis/DAVIS-data.zip")


def get_davis_17_test(dataset_dir):
    return _get_davis(dataset_dir, "DAVIS17-test", "https://data.vision.ee.ethz.ch/csergi/share/davis/DAVIS-2017-test-dev-480p.zip")


def get_davis_17(dataset_dir):
    return _get_davis(dataset_dir, "DAVIS17", "https://data.vision.ee.ethz.ch/csergi/share/davis/DAVIS-2017-trainval-480p.zip")


def _get_davis(dataset_dir, folder, url):
    """
    Returns the local path to the DAVIS dataset, given its root directory. The dataset
    is downloaded if not found on disk.
    :param dataset_dir: Path to the dataset directory
    :return: Path to the DAVIS dataset
    """
    davis_dir = join(dataset_dir, folder)
    tmp_dir = join(dataset_dir, 'tmp')

    if not exists(davis_dir):

        if not exists(dataset_dir):
            makedirs(dataset_dir)

        if not exists(tmp_dir):
            makedirs(tmp_dir)

        print("===> Downloading {}...".format(folder))
        response = urllib.request.urlopen(url)
        zip_path = join(dataset_dir, basename(url))
        with open(zip_path, 'wb') as f:
            f.write(response.read())

        zip_ref = zipfile.ZipFile(zip_path, 'r')

        print("===> Extracting data...")
        zip_ref.extractall(tmp_dir)
        zip_ref.close()

        # Move folder to desired path
        extracted_folder = join(tmp_dir, listdir(tmp_dir)[0])
        rename(extracted_folder, davis_dir)

        # Remove temporary files
        remove(zip_path)
        rmdir(tmp_dir)

    return davis_dir

def tuples_from_mv(dataset_dir):
    mv_dirs_names = [x for x in listdir(dataset_dir)]
    mv_dirs_names = [x for x in mv_dirs_names if x[0: 5] == 'cam1_']

    cam1_folders = [join(dataset_dir, x) for x in mv_dirs_names]

    tuples = []
    #i = 0
    for folder_cam1_path in cam1_folders:
        cam1_images = [join(folder_cam1_path, x) for x in listdir(folder_cam1_path)]
        cam1_images = [x for x in cam1_images if is_image(x)]

        for cam1_image in cam1_images:
            x1, t, x2 = cam1_image, \
                            cam1_image.replace('cam1_', 'cam2_', 1).replace('Image_1_', 'Image_2_', 1), \
                            cam1_image.replace('cam1_', 'cam3_', 1).replace('Image_1_', 'Image_3_', 1)
            tuples.append((x1, t, x2))
     #       i = i + 1
     #       if (i == 5):
     #           break

     #   i = 0

    return tuples

def tuples_from_davis(davis_dir, res='480p'):
    """
    Finds all images of the specified resolution from the DAVIS dataset. The found paths
    are returned as tuples of three elements.
    :param davis_dir: Path to the DAVIS dataset directory
    :param res: Resolution of the DAVIS images (either '480p' or '1080p')
    :return: List of paths as tuples (path_to_left, path_to_middle, path_to_right)
    """

    subdir = join(davis_dir, "JPEGImages/" + res)

    video_dirs = [join(subdir, x) for x in listdir(subdir)]
    video_dirs = [x for x in video_dirs if isdir(x)]

    tuples = []
    for video_dir in video_dirs:

        frame_paths = [join(video_dir, x) for x in listdir(video_dir)]
        frame_paths = [x for x in frame_paths if is_image(x)]
        frame_paths.sort()

        for i in range(len(frame_paths) // 3):
            x1, t, x2 = frame_paths[i * 3], frame_paths[i * 3 + 1], frame_paths[i * 3 + 2]
            tuples.append((x1, t, x2))

    return tuples

def get_selected_mv(dataset_dir=None):

    if dataset_dir is None:
        dataset_dir = config.DATASET_DIR

    root = join(dataset_dir, 'VISUAL')

    tuples = [
        ('Image_1_1407_0018.png', 'Image_2_1407_0018.png', 'Image_2_1407_0018.png')
    ]

    return [tuple(join(root, y) for y in x) for x in tuples]

def get_selected_davis(dataset_dir=None, res='480p'):

    if dataset_dir is None:
        dataset_dir = config.DATASET_DIR

    davis16_dir = get_davis_16(dataset_dir)
    root = join(davis16_dir, 'JPEGImages', res)

    tuples = [
        ('horsejump-low/00030.jpg', 'horsejump-low/00031.jpg', 'horsejump-low/00032.jpg'),
        ('parkour/00069.jpg', 'parkour/00070.jpg', 'parkour/00071.jpg'),
        ('breakdance/00060.jpg', 'breakdance/00061.jpg', 'breakdance/00062.jpg'),
        ('drift-turn/00045.jpg', 'drift-turn/00046.jpg', 'drift-turn/00047.jpg'),
        ('rhino/00027.jpg', 'rhino/00028.jpg', 'rhino/00029.jpg'),
        ('motocross-jump/00009.jpg', 'motocross-jump/00010.jpg', 'motocross-jump/00011.jpg'),
        ('flamingo/00006.jpg', 'flamingo/00007.jpg', 'flamingo/00008.jpg'),
        ('scooter-black/00027.jpg', 'scooter-black/00028.jpg', 'scooter-black/00029.jpg'),
        ('boat/00006.jpg', 'boat/00007.jpg', 'boat/00008.jpg'),
        ('dance-twirl/00054.jpg', 'dance-twirl/00055.jpg', 'dance-twirl/00056.jpg')
    ]

    return [tuple(join(root, y) for y in x) for x in tuples]


########################################## PATCH EXTRACTION #########################################

def simple_flow(frame1, frame2):
    """
    Runs SimpleFlow given two consecutive frames.
    :param frame1: Numpy array of the frame at time t
    :param frame2: Numpy array of the frame at time t+1
    :return: Numpy array with the flow for each pixel. Shape is same as input
    """
    flow = cv.optflow.calcOpticalFlowSF(frame1, frame2, layers=3, averaging_block_size=2, max_flow=4)
    n = np.sum(1 - np.isnan(flow), axis=(0, 1))
    flow[np.isnan(flow)] = 0
    return np.linalg.norm(np.sum(flow, axis=(0, 1)) / n)


def is_jumpcut(frame1, frame2, threshold=np.inf):
    """
    Detects a jumpcut between the two frames.
    :param frame1: Numpy array of the frame at time t
    :param frame2: Numpy array of the frame at time t+1
    :param threshold: Maximum difference allowed for the frames to be considered consecutive
    :return: Whether or not there is a jumpcut between the two frames
    """
    pixels_per_channel = frame1.size / 3
    hist = lambda x: np.histogram(x.reshape(-1), 8, (0, 255))[0] / pixels_per_channel
    err = lambda a, b: ((hist(a) - hist(b)) ** 2).mean()

    return err(frame1[:, :, 0], frame2[:, :, 0]) > threshold or \
           err(frame1[:, :, 1], frame2[:, :, 1]) > threshold or \
           err(frame1[:, :, 2], frame2[:, :, 2]) > threshold

def same_image(frame1, frame2, threshold=np.inf):
    pixels_per_channel = frame1.size / 3
    hist = lambda x: np.histogram(x.reshape(-1), 8, (0, 255))[0] / pixels_per_channel
    err = lambda a, b: ((hist(a) - hist(b)) ** 2).mean()

    return err(frame1[:, :, 0], frame2[:, :, 0]) < threshold and \
           err(frame1[:, :, 1], frame2[:, :, 1]) < threshold and \
           err(frame1[:, :, 2], frame2[:, :, 2]) < threshold

def _extract_patches_worker(tuples, max_per_frame=1, trials_per_tuple=100, flow_threshold=0.0,
                            jumpcut_threshold=np.inf):
    """
    Extracts small patches from the original frames. The patches are selected to maximize
    their contribution to the training.
    :param tuples: List of tuples containing the input frames as (left, middle, right)
    :param max_per_frame: Maximum number of patches that can be extracted from a frame
    :param trials_per_tuple: Number of random crops to test for each tuple
    :param flow_threshold: Minimum average optical flow for a patch to be selected
    :param jumpcut_threshold: ...
    :return: List of dictionaries representing each patch
    """

    patch_h, patch_w = config.PATCH_SIZE
    n_tuples = len(tuples)
    all_patches = []
    jumpcuts = 0
    flowfiltered = 0
    total_iters = n_tuples * trials_per_tuple

    pil_to_numpy = lambda x: np.array(x)[:, :, ::-1]

    # load green image for testing jumpcuts
    greenImage = load_img('/media/lera/ADATA HV320/mv_output/entireGreen_0.jpg')
    greenImage = pil_to_numpy(greenImage)

    for tup_index in range(n_tuples):
        tup = tuples[tup_index]

        left, middle, right = (load_img(x) for x in tup)
        img_w, img_h = left.size

        left = pil_to_numpy(left)
        middle = pil_to_numpy(middle)
        right = pil_to_numpy(right)

        selected_patches = []

        for count in range(trials_per_tuple):

            i = random.randint(0, img_h - patch_h)
            j = random.randint(0, img_w - patch_w)

            left_patch = left[i:i + patch_h, j:j + patch_w, :]
            right_patch = right[i:i + patch_h, j:j + patch_w, :]
            middle_patch = middle[i:i + patch_h, j:j + patch_w, :]

            # Allow green images for the first 5 trials
            if (count > 0):
                if (same_image(left_patch, greenImage, jumpcut_threshold) and \
                       same_image(middle_patch, greenImage, jumpcut_threshold)) or \
                        (same_image(middle_patch, greenImage, jumpcut_threshold) and \
                         same_image(right_patch, greenImage, jumpcut_threshold)) :
                    jumpcuts += 1
                    continue

            avg_flow = simple_flow(left_patch, right_patch)
            #if random.random() > avg_flow / flow_threshold:
            #    flowfiltered += 1
            #    continue

            selected_patches.append({
                "left_frame": tup[0],
                "middle_frame": tup[1],
                "right_frame": tup[2],
                "patch_i": i,
                "patch_j": j,
                "avg_flow": avg_flow
            })

        sorted(selected_patches, key=lambda x: x['avg_flow'], reverse=True)
        all_patches += selected_patches[:max_per_frame]
        # print("===> Tuple {}/{} ready.".format(tup_index+1, n_tuples))

    print('===> Processed {} tuples, {} patches extracted, {} discarded as jumpcuts, {} filtered by flow'.format(
        n_tuples, len(all_patches), 100.0 * jumpcuts / total_iters, 100.0 * flowfiltered / total_iters
    ))

    return all_patches


def _extract_patches(tuples, max_per_frame=1, trials_per_tuple=100, flow_threshold=25.0, jumpcut_threshold=np.inf,
                     workers=0):
    """
    Spawns the specified number of workers running _extract_patches_worker().
    Call this with workers=0 to run on the current thread.
    """

    tick_t = timer()
    print('===> Extracting patches...')

    if workers != 0:
        parallel = Parallel(n_jobs=workers, backend='threading', verbose=5)
        tuples_per_job = len(tuples) // workers + 1
        result = parallel(
            delayed(_extract_patches_worker)(tuples[i:i + tuples_per_job], max_per_frame, trials_per_tuple,
                                             flow_threshold, jumpcut_threshold) for i in
            range(0, len(tuples), tuples_per_job))
        patches = sum(result, [])
    else:
        patches = _extract_patches_worker(tuples, max_per_frame, trials_per_tuple, flow_threshold, jumpcut_threshold)

    tock_t = timer()
    print("Done. Took ~{}s".format(round(tock_t - tick_t)))

    return patches


############################################### CACHE ###############################################

def get_cached_patches(dataset_dir=None):
    """
    Finds the cached patches (stored as images) from disk and returns their paths as a list of tuples
    :param dataset_dir: Path to the dataset folder
    :return: List of paths to patches as tuples (path_to_left, path_to_middle, path_to_right)
    """

    if dataset_dir is None:
        dataset_dir = config.DATASET_DIR

    cache_dir = join(dataset_dir, 'cache')

    frame_paths = [join(cache_dir, x) for x in listdir(cache_dir)]
    frame_paths = [x for x in frame_paths if is_image(x)]
    frame_paths.sort()

    tuples = []

    for i in range(len(frame_paths) // 3):
        x1, t, x2 = frame_paths[i * 3], frame_paths[i * 3 + 1], frame_paths[i * 3 + 2]
        tuples.append((x1, t, x2))

    return tuples


def _cache_patches_worker(cache_dir, patches):
    """
    Writes to disk the specified patches as images.
    :param cache_dir: Path to the cache folder
    :param patches: List of patches
    """
    count = 0
    for p in patches:
        #patch_id = str(random.randint(1e10, 1e16))
        if (count > 300):
            break

        frames = load_patch(p)

        filenames = []
        leftFrame = p['left_frame'].split("/")
        leftFrame_name = leftFrame[len(leftFrame) - 1]
        leftFrame_name = leftFrame_name[8: len(leftFrame_name)-4]
        #filenames[0] = leftFrame_name
        #filenames[1] = leftFrame_name.replace("Image_1", "Image_2", 1)
        #filenames[2] = leftFrame_name.replace("Image_1", "Image_3", 1)

        for i in range(3):
            file_name = '{}_x{}_y{}_{}.jpg'.format(leftFrame_name, p['patch_i'], p['patch_j'], i)
            frames[i].save(join(cache_dir, file_name))

        count=count+1




def _cache_patches(cache_dir, patches, workers=0):
    """
    Spawns the specified number of workers running _cache_patches_worker().
    Call this with workers=0 to run on the current thread.
    """

    if exists(cache_dir):
        rmdir(cache_dir)

    makedirs(cache_dir)

    tick_t = timer()
    print('===> Caching patches...')

    if workers != 0:
        parallel = Parallel(n_jobs=workers, backend='threading', verbose=5)
        patches_per_job = len(patches) // workers + 1
        parallel(delayed(_cache_patches_worker)(cache_dir, patches[i:i + patches_per_job]) for i in
                 range(0, len(patches), patches_per_job))
    else:
        _cache_patches_worker(cache_dir, patches)

    tock_t = timer()
    print("Done. Took ~{}s".format(round(tock_t - tick_t)))


################################################ MAIN ###############################################

def prepare_dataset(dataset_dir=None, force_rebuild=False):
    """
    Performs all necessary operations to get the training dataset ready, such as
    selecting patches, caching the cropped versions if necessary, etc..
    :param dataset_dir: Path to the dataset folder
    :param force_rebuild: Whether or not the patches should be extracted again, even if a cached version exists on disk
    :return: List of patches
    """

    if dataset_dir is None:
        dataset_dir = config.DATASET_DIR

    workers = config.NUM_WORKERS
    json_path = join(dataset_dir, 'patches.json')
    cache_dir = join(dataset_dir, 'cache')

    if exists(json_path) and not force_rebuild:

        print('===> Patches already processed, reading from JSON...')
        with open(json_path) as f:
            patches = json.load(f)

        if config.CACHE_PATCHES and not exists(cache_dir):
            _cache_patches(cache_dir, patches, workers)

        return patches

    #davis_dir = get_davis_17(dataset_dir)
    mv_dir = join(dataset_dir, 'DATA')
    tuples = tuples_from_mv(mv_dir)

    patches = _extract_patches(
        tuples,
        max_per_frame=20,
        trials_per_tuple=200,
        flow_threshold=25.0,
        jumpcut_threshold=15e-3,
        workers=2
    )

    # shuffle patches before writing to file
    random.shuffle(patches)

    print('===> Saving JSON...')
    with open(json_path, 'w') as f:
        json.dump(patches, f)

    if config.CACHE_PATCHES:
        _cache_patches(cache_dir, patches, workers)

    return patches

def map_steps(min=5, max=50, stepSize=5):
    nSteps = int((max - min) / stepSize )

    mapSteps = None

    for step in range(0, nSteps):
        step_min = min + stepSize * step
        step_max = step_min + stepSize
        newStep = np.array((step, step_min, step_max))

        if mapSteps is None:
            mapSteps = newStep
        else:
            mapSteps = np.vstack((mapSteps, newStep))

    return nSteps, mapSteps

def get_tuples_distance(dataset_dir=None, test_set=False, minDistance=1, maxDistance=10, stepSize=1):
    if dataset_dir is None:
        dataset_dir = config.DATASET_DIR

    if test_set:
        mv_dir = join(dataset_dir, 'TEST')
    else:
        mv_dir = join(dataset_dir, 'VALIDATION')

    npy_file_location = join(mv_dir,"per_frame_8.npy")
    npy_file = np.load(npy_file_location)

    (nSteps, mapSteps) = map_steps(minDistance, maxDistance, stepSize)

    # find rows in npy_file that correspond to the step
    step_column_index = 0
    min_column_index = 1
    max_column_index = 2
    run_column_index = 0
    frame_column_index = 1
    distance_column_index = 2

    steps_dict = dict()

    # create dictionary for runs and frames in each step
    for step in range(nSteps):
         steps_dict[step] = []

    for step in mapSteps:
        for i in range(137): # len(npy_file)):
            if npy_file[i][0][distance_column_index] >= step[min_column_index] and npy_file[i][0][distance_column_index] <= \
                    step[max_column_index]:
                steps_dict.get(step[step_column_index]).append((npy_file[i][0][run_column_index], npy_file[i][0][frame_column_index]))

    return steps_dict, mapSteps

def get_tuples_offset(dataset_dir=None, test_set=False, minOffset=5, maxOffset=50, stepSize=5):

    if dataset_dir is None:
        dataset_dir = config.DATASET_DIR

    if test_set:
        mv_dir = join(dataset_dir, 'TEST')
    else:
        mv_dir = join(dataset_dir, 'VALIDATION')

    #tuples = tuples_from_mv(mv_dir)

    npy_file_location = join(mv_dir,"cameraParams_8.npy")
    npy_file = np.load(npy_file_location)

    (nSteps, mapSteps) = map_steps(minOffset, maxOffset, stepSize)

    run_to_step = dict()

    # returns mapping from runNumber to offset step number according to mapping
    runnumber_column_index = 0
    offset_column_index = 8
    step_column_index = 0
    min_column_index = 1
    max_column_index = 2

    # find rows in npy_file that correspond to the step
    for step in mapSteps:
        for i in range(137):  #len(npy_file)):
            if npy_file[i][0][offset_column_index] >= step[min_column_index] and npy_file[i][0][offset_column_index] <= step[max_column_index]:
                run_to_step[int(npy_file[i][0][runnumber_column_index])] = int(step[step_column_index])

    return run_to_step, mapSteps

    # tuples_dict = dict()
    #
    # # create dictionary for tuple indices
    # for step in range(nSteps):
    #     tuples_dict[step] = []
    #
    # #for index in len(tuples):
    # for i, tuple in enumerate(tuples):
    #     tuple_name = tuple[0].split("/")
    #     run_number = int(tuple_name[len(tuple_name) - 1][8:12])
    #
    #     # find stepNumber for tuple
    #     if run_number in run_to_step:
    #         stepNumber = run_to_step.get(run_number)
    #         tuples_dict.get(stepNumber).append(i)  #tuple)
    #
    # return tuples_dict, mapSteps


def prepare_dataset_validation(dataset_dir=None, test_set=False, number_of_samples = 100, randomSelection=True):
    """
    Performs all necessary operations to get the training dataset ready, such as
    selecting patches, caching the cropped versions if necessary, etc..
    :param dataset_dir: Path to the dataset folder
    :param force_rebuild: Whether or not the patches should be extracted again, even if a cached version exists on disk
    :return: List of patches
    """
    if dataset_dir is None:
        dataset_dir = config.DATASET_DIR

    if test_set:
        mv_dir = join(dataset_dir, 'TEST')
    else:
        mv_dir = join(dataset_dir, 'VALIDATION')
		
    tuples = tuples_from_mv(mv_dir)

    if randomSelection != True:
        return  tuples

    n_samples = len(tuples)

    #if randomSelection:
    n_samples = min(len(tuples), number_of_samples)  # config.MAX_VALIDATION_SAMPLES)
    random_ = random.Random(42)
    tuples = random_.sample(tuples, n_samples) # * 2)

    # find n_samples images that are not all green
    #jumpcut_threshold = 60e-6  # Good - 15e-6
    #pil_to_numpy = lambda x: np.array(x)[:, :, ::-1]

    #bk_image = '/home/lera/Documents/surreal-master/datageneration/misc/background/just_green_test.png'
    #greenImage = load_img(bk_image)
    #greenImage = pil_to_numpy(greenImage)
    #count = 0

    # non_green_tuples = []
    #
    # for i, tup in enumerate(tuples):
    #     x1, gt, x2 = [pil_to_numpy(load_img(p)) for p in tup]
    #
    #     if (same_image(x1, greenImage, jumpcut_threshold) and \
    #         same_image(gt, greenImage, jumpcut_threshold)) or \
    #             (same_image(gt, greenImage, jumpcut_threshold) and \
    #              same_image(x2, greenImage, jumpcut_threshold)):
    #         continue
    #
    #     non_green_tuples.append(tup)
    #     count+=1
    #
    #     if count == n_samples:
    #         break

    return tuples # non_green_tuples