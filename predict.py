import sys
sys.path.insert(0, "../.local/lib/python3.5/site-packages/")

import os
# os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID" # see issue #152
# os.environ["CUDA_VISIBLE_DEVICES"] = ""

import util
import json
import numpy as np
import skimage.io as io
import unet
import datasets
import patchmaker

rationale = """
Test out predict.py refactor.
"""

predict_params = {
 'savedir' : './',
 'grey_tif_folder' : "data3/labeled_data_cellseg/greyscales/",
 'batch_size' : 1,
 'step' : 1900,
 'itd' : 24, # border width
 'width' : 1900,
}

def get_params_from_dir(predict_params, direc):
    pp = predict_params
    pp['model_weights'] = direc + '/unet_model_weights_checkpoint.h5'
    train_params = json.load(open(direc + '/train_params.json'))
    for key in ['n_convolutions_first_layer', 'n_pool', 'n_classes', 'dropout_fraction']:
        pp[key] = train_params[key]
    return pp

def predict(predict_params, model=None):
    pp = predict_params
    if not model:
        model = unet.get_unet_n_pool(pp['n_pool'], 
                                 #n_classes = pp['n_classes'],
                                 n_convolutions_first_layer = pp['n_convolutions_first_layer'],
                                 dropout_fraction = pp['dropout_fraction'])
        model.load_weights(pp['model_weights'])

    print(model.summary())

    predict_image_names = util.sglob(pp['grey_tif_folder'] + '*.tif')

    for name in predict_image_names[:5]:
        img = io.imread(name)
        print(name, img.shape)
        res = predict_single_image(model, img, pp)
        print("Res shape", res.shape)
        combo = np.stack((img, res), axis=0)
        path, base, ext =  util.path_base_ext(name)
        io.imsave(pp['savedir'] + "/" + base + '_predict_' + ext, combo.astype('float32'))

def predict_single_image(model, img, pp):
    "unet predict on a greyscale img"

    coords = patchmaker.square_grid_coords(img, pp['step'])
    w = pp['width']
    X = patchmaker.sample_patches_from_img(coords, img, (w,w))
    # a,b = img.shape
    # n = pp['n_pool']
    # am = a%(2**n)
    # bm = b%(2**n)
    # X = img[np.newaxis, :-am, :-bm]
    X = unet.normalize_X(X)
    X = unet.add_singleton_dim(X)
    Y_pred = model.predict(X, batch_size=pp['batch_size'])

    if Y_pred.ndim == 3:
        print("NDIM 3, ")
        Y_pred = Y_pred.reshape((-1, pp['width'], pp['width'], pp['n_classes']))

    Y_pred = Y_pred[...,1]
    #Y_new = np.zeros((a,b))
    #Y_new[:-am, :-bm] = Y_pred
    #print("Y_pred shape: ", Y_pred.shape)
    #io.imsave('Ypred.tif', Y_pred)

    res = patchmaker.piece_together(Y_pred, coords, imgshape=img.shape, border=pp['itd'])
    return res[...,0].astype(np.float32)
    #return Y_new

def normalize_and_predict_stakk_for_scores(model, stakk):
    xs = stakk[:,0]
    ys = stakk[:,1]
    xs = xs.astype('float32')
    xs = unet.normalize_X(xs)
    ys = fix_labels(ys)
    xs = unet.normalize_X(xs)
    xs = unet.add_singleton_dim(xs)
    ypred = model.predict(xs)
    ypred = np.argmax(ypred, axis=-1)
    masks = ys == ypred
    scores = np.sum(masks, axis=(1,2))
    return scores

if __name__ == '__main__':
    predict_params = get_params_from_dir(predict_params, sys.argv[1])
    predict_params['savedir'] = sys.argv[2]
    predict(predict_params)

