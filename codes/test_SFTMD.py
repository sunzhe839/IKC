import os.path
import logging
import time
import argparse
from collections import OrderedDict
import numpy as np

import options.options as option
import utils.util as util
from data.util import bgr2ycbcr
from data import create_dataset, create_dataloader
from models import create_model

#### options
parser = argparse.ArgumentParser()
parser.add_argument('-opt_F', type=str, required=True, help='Path to options YMAL file.')
opt_F = option.parse(parser.parse_args().opt_F, is_train=False)
opt_F = option.dict_to_nonedict(opt_F)

#### mkdir and logger
util.mkdirs((path for key, path in opt_F['path'].items() if not key == 'experiments_root'
             and 'pretrain_model' not in key and 'resume' not in key))
util.setup_logger('base', opt_F['path']['log'], 'test_' + opt_F['name'], level=logging.INFO,
                  screen=True, tofile=True)
logger = logging.getLogger('base')
logger.info(option.dict2str(opt_F))

# create PCA matrix of enough kernel
batch_ker = util.random_batch_kernel(batch=30000, l=21, sig_min=0.2, sig_max=4.0, rate_iso=1.0, scaling=3, tensor=False)
print('batch kernel shape: {}'.format(batch_ker.shape))
b = np.size(batch_ker, 0)
batch_ker = batch_ker.reshape((b, -1))
pca_matrix = util.PCA(batch_ker, k=10).float()
print('PCA matrix shape: {}'.format(pca_matrix.shape))

#### Create test dataset and dataloader
test_loaders = []
for phase, dataset_opt in sorted(opt_F['datasets'].items()):
    test_set = create_dataset(dataset_opt)
    test_loader = create_dataloader(test_set, dataset_opt)
    logger.info('Number of test images in [{:s}]: {:d}'.format(dataset_opt['name'], len(test_set)))
    test_loaders.append(test_loader)

# load pretrained model by default
model_F = create_model(opt_F)

for test_loader in test_loaders:
    test_set_name = test_loader.dataset.opt['name']#path opt['']
    logger.info('\nTesting [{:s}]...'.format(test_set_name))
    test_start_time = time.time()
    dataset_dir = os.path.join(opt_F['path']['results_root'], test_set_name)
    util.mkdir(dataset_dir)

    test_results = OrderedDict()
    test_results['psnr'] = []
    test_results['ssim'] = []
    test_results['psnr_y'] = []
    test_results['ssim_y'] = []

    for test_data in test_loader:
        need_GT = False if test_loader.dataset.opt['dataroot_GT'] is None else True
        img_path = test_data['GT_path'][0] if need_GT else test_data['LQ_path'][0]
        img_name = os.path.splitext(os.path.basename(img_path))[0]
        #### preprocessing for LR_img and kernel map
        prepro = util.SRMDPreprocessing(opt_F['scale'], pca_matrix, para_input=15, noise=False, cuda=True,
                                        sig_min=0.2, sig_max=4.0, rate_iso=1.0, scaling=3,
                                        rate_cln=0.2, noise_high=0.0)
        LR_img, ker_map = prepro(test_data['GT'])

        model_F.feed_data(test_data, LR_img, ker_map)
        model_F.test()

        F_visuals = model_F.get_current_visuals()

        sr_img = util.tensor2img(F_visuals['SR'])  # uint8

        # save images
        suffix = opt_F['suffix']
        if suffix:
            save_img_path = os.path.join(dataset_dir, img_name + suffix + '.png')
        else:
            save_img_path = os.path.join(dataset_dir, img_name + '.png')
        util.save_img(sr_img, save_img_path)

        # calculate PSNR and SSIM
        if need_GT:
            gt_img = util.tensor2img(F_visuals['GT'])
            gt_img = gt_img / 255.
            sr_img = sr_img / 255.

            crop_border = opt_F['crop_border'] if opt_F['crop_border'] else opt_F['scale']
            if crop_border == 0:
                cropped_sr_img = sr_img
                cropped_gt_img = gt_img
            else:
                cropped_sr_img = sr_img[crop_border:-crop_border, crop_border:-crop_border, :]
                cropped_gt_img = gt_img[crop_border:-crop_border, crop_border:-crop_border, :]

            psnr = util.calculate_psnr(cropped_sr_img * 255, cropped_gt_img * 255)
            ssim = util.calculate_ssim(cropped_sr_img * 255, cropped_gt_img * 255)
            test_results['psnr'].append(psnr)
            test_results['ssim'].append(ssim)

            if gt_img.shape[2] == 3:  # RGB image
                sr_img_y = bgr2ycbcr(sr_img, only_y=True)
                gt_img_y = bgr2ycbcr(gt_img, only_y=True)
                if crop_border == 0:
                    cropped_sr_img_y = sr_img_y
                    cropped_gt_img_y = gt_img_y
                else:
                    cropped_sr_img_y = sr_img_y[crop_border:-crop_border, crop_border:-crop_border]
                    cropped_gt_img_y = gt_img_y[crop_border:-crop_border, crop_border:-crop_border]
                psnr_y = util.calculate_psnr(cropped_sr_img_y * 255, cropped_gt_img_y * 255)
                ssim_y = util.calculate_ssim(cropped_sr_img_y * 255, cropped_gt_img_y * 255)
                test_results['psnr_y'].append(psnr_y)
                test_results['ssim_y'].append(ssim_y)
                logger.info(
                    '{:20s} - PSNR: {:.6f} dB; SSIM: {:.6f}; PSNR_Y: {:.6f} dB; SSIM_Y: {:.6f}.'.
                    format(img_name, psnr, ssim, psnr_y, ssim_y))
            else:
                logger.info('{:20s} - PSNR: {:.6f} dB; SSIM: {:.6f}.'.format(img_name, psnr, ssim))
        else:
            logger.info(img_name)

    if need_GT:  # metrics
        # Average PSNR/SSIM results
        ave_psnr = sum(test_results['psnr']) / len(test_results['psnr'])
        ave_ssim = sum(test_results['ssim']) / len(test_results['ssim'])
        logger.info(
            '----Average PSNR/SSIM results for {}----\n\tPSNR: {:.6f} dB; SSIM: {:.6f}\n'.format(
                test_set_name, ave_psnr, ave_ssim))
        if test_results['psnr_y'] and test_results['ssim_y']:
            ave_psnr_y = sum(test_results['psnr_y']) / len(test_results['psnr_y'])
            ave_ssim_y = sum(test_results['ssim_y']) / len(test_results['ssim_y'])
            logger.info(
                '----Y channel, average PSNR/SSIM----\n\tPSNR_Y: {:.6f} dB; SSIM_Y: {:.6f}\n'.
                format(ave_psnr_y, ave_ssim_y))



def SFTMD_test(opt_F):

    # config loggers. Before it, the log will not work
    util.setup_logger('base', opt_F['path']['log'], 'test_' + opt_F['name'], level=logging.INFO,
                      screen=True, tofile=True)
    logger = logging.getLogger('base')
    logger.info(option.dict2str(opt_F))

    for phase, dataset_opt in sorted(opt_F['datasets'].items()):
        test_set = create_dataset(dataset_opt)
        test_loader = create_dataloader(test_set, dataset_opt, opt_F)
        logger.info('Number of test images in [{:s}]: {:d}'.format(dataset_opt['name'], len(test_set)))

        model_F = create_model(opt_F)

        logger.info('\nTesting [{:s}]...'.format(opt_F['model']))
        dataset_dir = os.path.join(opt_F['path']['results_root'], dataset_opt['name'])
        util.mkdir(dataset_dir)

        avg_psnr = 0.0
        idx = 0
        for _, data in enumerate(test_loader):
            idx += 1
            model_F.feed_data(data)
            model_F.test()

            visuals = model_F.get_current_visuals()
            gt_img = util.tensor2img(visuals['GT'])  # uint8
            sr_img = util.tensor2img(visuals['SR'])  # uint8

            # Save SR images for reference
            img_name = os.path.splitext(os.path.basename(data['LQ_path'][0]))[0]
            img_dir = os.path.join(dataset_dir, img_name)
            util.mkdir(img_dir)

            save_img_path = os.path.join(img_dir, '{}.png'.format(img_name))
            util.save_img(sr_img, save_img_path)

            # calculate PSNR
            crop_size = opt_F['scale']
            gt_img = gt_img / 255.
            sr_img = sr_img / 255.
            cropped_sr_img = sr_img[crop_size:-crop_size, crop_size:-crop_size, :]
            cropped_gt_img = gt_img[crop_size:-crop_size, crop_size:-crop_size, :]
            avg_psnr += util.calculate_psnr(cropped_sr_img * 255, cropped_gt_img * 255)

            avg_psnr = avg_psnr / idx

            # log
            logger.info('# test # PSNR: {:.4e}'.format(avg_psnr))

    print('SR image of SFTMD has been saved to list')

