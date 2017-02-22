doc="""
UNET architecture for pixelwise classification
"""

import numpy as np
import skimage.io as io

from keras.models import Model
from keras.layers import Input, merge, Convolution2D, MaxPooling2D, UpSampling2D, Reshape, core, Dropout
from keras.optimizers import Adam
from keras.callbacks import ModelCheckpoint, LearningRateScheduler, EarlyStopping
from keras import backend as K
from keras.utils import np_utils
# from keras.utils.visualize_util import plot
from keras.optimizers import SGD

import skimage.util as skut
import util

# global variables for patch dimensions and stride
x_width = 48
y_width = 48
step = 10


# setup X and Y for feeding into the model

def sample_patches_from_img(coords, img):
    assert coords[:,0].max() <= img.shape[0]-x_width
    assert coords[:,1].max() <= img.shape[1]-y_width
    patches = np.zeros(shape=(coords.shape[0], x_width, y_width), dtype=img.dtype)
    for m,ind in enumerate(coords):
        patches[m] = img[ind[0]:ind[0]+x_width, ind[1]:ind[1]+y_width]
    return patches

def random_patch_coords(img, n):
    xc = np.random.randint(img.shape[0]-x_width, size=n)
    yc = np.random.randint(img.shape[1]-y_width, size=n)
    return np.stack((xc, yc), axis=1)

def regular_patch_coords(img):
    dx,dy = img.shape[0]-x_width, img.shape[1]-y_width
    coords = skut.view_as_windows(np.indices((dx+1,dy+1)), (2,1,1), step=step)
    a,b,c,d,e,f = coords.shape
    return coords.reshape(b*c,2)

def rebuild_img_from_patches(zeros_img, patches, coords):
    "TODO: potentially add more ways of recombining than a simple average, i.e. maximum"
    count_img = np.zeros_like(zeros_img)
    n_samp, dx, dy = patches.shape
    for cord,patch in zip(coords, patches):
        x,y = cord
        zeros_img[x:x+dx, y:y+dy] += patch
        count_img[x:x+dx, y:y+dy] += np.ones_like(patch)
    return zeros_img/count_img

def rebuild_img_from_patch_activations((x,y), patchact, coords):
    "TODO: potentially add more ways of recombining than a simple average, i.e. maximum"
    # TODO: this will break.
    n_samp, dx, dy, nclasses = patchact.shape
    zeros_img = np.zeros(shape=(x,y,nclasses))
    count_img = np.zeros(shape=(x,y,nclasses))
    print("Nans?: ", np.sum(map(util.count_nans, patchact)))
    for cord,patch in zip(coords, patchact):
        x,y = cord
        zeros_img[x:x+dx, y:y+dy] += patch
        count_img[x:x+dx, y:y+dy] += np.ones_like(patch)
    print(map(util.count_nans, [zeros_img, count_img]))
    res = zeros_img/count_img
    res[res==np.nan] = -1
    return zeros_img/count_img

def imglist_to_X(greylist):
    """turn list of images into ndarray of patches, labels and their coordinates. Used for
    both training and testing."""

    coords = map(regular_patch_coords, greylist)
    f = lambda (c,g): sample_patches_from_img(c,g)
    greypatches = map(f, zip(coords, greylist))
    X = np.concatenate(tuple(greypatches), axis=0)

    # reshape into theano dimension ordering
    a,b,c = X.shape
    assert K.image_dim_ordering() == 'th'
    X = X.reshape(a, 1, b, c)

    # normalize X per patch
    mi = np.amin(X,axis = (1,2,3), keepdims = True)
    ma = np.amax(X,axis = (1,2,3), keepdims = True)+1.e-10
    X = (X-mi)/(ma-mi)
    return X.astype(np.float32)

def imglist_to_Y(labellist):
    "turn list of images into ndarray of patches, labels and their coordinates"

    coords = map(regular_patch_coords, labellist)
    f = lambda (c,g): sample_patches_from_img(c,g)
    labelpatches = map(f, zip(coords, labellist))
    Y = np.concatenate(tuple(labelpatches), axis=0)

    # reshape into theano dimension ordering
    a,b,c = Y.shape
    assert K.image_dim_ordering() == 'th'
    Y = Y.reshape(a, 1, b, c)

    # convert label values to vector of label scores
    Y[Y==2] = 1
    assert Y.min() == 0
    nb_classes = Y.max()+1
    Y = Y.reshape(a*b*c)
    Y = np_utils.to_categorical(Y, nb_classes)
    Y = Y.reshape(a, b*c, nb_classes)
    return Y.astype(np.float32)

def imglists_to_XY(greylist, labellist):
    X = imglist_to_X(greylist)
    Y = imglist_to_Y(labellist)
    return X,Y

def process_XY_for_training(X,Y):
    assert X.ndim==4
    inds = np.mean(X, axis=(1,2,3)) > 0.5
    return X,Y

# setup and train the model

def my_categorical_crossentropy(weights =(1., 1.)):
    def catcross(y_true, y_pred):
        return -(weights[0] * K.mean(y_true[:,:,0]*K.log(y_pred[:,:,0]+K.epsilon())) +
                 weights[1] * K.mean(y_true[:,:,1]*K.log(y_pred[:,:,1]+K.epsilon())))

        # return -(K.mean(y_true[:,:,0]*K.log(y_pred[:,:,0]+K.epsilon()))+K.mean(y_true[:,:,1]*K.log(y_pred[:,:,1]+K.epsilon())))
    return catcross

def get_unet():
    inputs = Input((1, y_width, x_width))
    conv1 = Convolution2D(32, 3, 3, activation='relu', border_mode='same')(inputs)
    conv1 = Dropout(0.2)(conv1)
    conv1 = Convolution2D(32, 3, 3, activation='relu', border_mode='same')(conv1)
    pool1 = MaxPooling2D(pool_size=(2, 2))(conv1)
    #
    conv2 = Convolution2D(64, 3, 3, activation='relu', border_mode='same')(pool1)
    conv2 = Dropout(0.2)(conv2)
    conv2 = Convolution2D(64, 3, 3, activation='relu', border_mode='same')(conv2)
    pool2 = MaxPooling2D(pool_size=(2, 2))(conv2)
    #
    conv3 = Convolution2D(128, 3, 3, activation='relu', border_mode='same')(pool2)
    conv3 = Dropout(0.2)(conv3)
    conv3 = Convolution2D(128, 3, 3, activation='relu', border_mode='same')(conv3)

    up1 = merge([UpSampling2D(size=(2, 2))(conv3), conv2], mode='concat', concat_axis=1)
    conv4 = Convolution2D(64, 3, 3, activation='relu', border_mode='same')(up1)
    conv4 = Dropout(0.2)(conv4)
    conv4 = Convolution2D(64, 3, 3, activation='relu', border_mode='same')(conv4)
    #
    up2 = merge([UpSampling2D(size=(2, 2))(conv4), conv1], mode='concat', concat_axis=1)
    conv5 = Convolution2D(32, 3, 3, activation='relu', border_mode='same')(up2)
    conv5 = Dropout(0.2)(conv5)
    conv5 = Convolution2D(32, 3, 3, activation='relu', border_mode='same')(conv5)
    #
    # here nb_classes used to be just the number 2
    conv6 = Convolution2D(2, 1, 1, activation='relu',border_mode='same')(conv5)
    conv6 = core.Reshape((2, y_width*x_width))(conv6)
    conv6 = core.Permute((2,1))(conv6)
    ############
    conv7 = core.Activation('softmax')(conv6)

    model = Model(input=inputs, output=conv7)
    return model

def get_unet_2():
    inputs = Input((1, y_width, x_width))
    conv1 = Convolution2D(32, 3, 3, activation='relu', border_mode='same')(inputs)
    conv1 = Dropout(0.2)(conv1)
    conv1 = Convolution2D(32, 3, 3, activation='relu', border_mode='same')(conv1)
    pool1 = MaxPooling2D(pool_size=(2, 2))(conv1)
    #
    conv2 = Convolution2D(64, 3, 3, activation='relu', border_mode='same')(pool1)
    conv2 = Dropout(0.2)(conv2)
    conv2 = Convolution2D(64, 3, 3, activation='relu', border_mode='same')(conv2)
    pool2 = MaxPooling2D(pool_size=(2, 2))(conv2)
    #
    conv3 = Convolution2D(128, 3, 3, activation='relu', border_mode='same')(pool2)
    conv3 = Dropout(0.2)(conv3)
    conv3 = Convolution2D(128, 3, 3, activation='relu', border_mode='same')(conv3)

    up1 = merge([UpSampling2D(size=(2, 2))(conv3), conv2], mode='concat', concat_axis=1)
    conv4 = Convolution2D(64, 3, 3, activation='relu', border_mode='same')(up1)
    conv4 = Dropout(0.2)(conv4)
    conv4 = Convolution2D(64, 3, 3, activation='relu', border_mode='same')(conv4)
    #
    up2 = merge([UpSampling2D(size=(2, 2))(conv4), conv1], mode='concat', concat_axis=1)
    conv5 = Convolution2D(32, 3, 3, activation='relu', border_mode='same')(up2)
    conv5 = Dropout(0.2)(conv5)
    conv5 = Convolution2D(32, 3, 3, activation='relu', border_mode='same')(conv5)
    #
    # here nb_classes used to be just the number 2
    conv6 = Convolution2D(2, 1, 1, activation='relu',border_mode='same')(conv5)
    conv6 = core.Reshape((2, y_width*x_width))(conv6)
    conv6 = core.Permute((2,1))(conv6)
    ############
    conv7 = core.Activation('softmax')(conv6)

    model = Model(input=inputs, output=conv7)
    return model

def trainmodel(X, Y, model=None, batch_size = 128, nb_epoch = 1, patience = 5):
    """
    Note: Input X,Y should really just be training data! Not all the labeled data we have!
    """

    # this shuffles the data (if we're gonna augment data, do it now)
    train_ind, test_ind = util.subsample_ind(X,Y,0.2)
    np.random.shuffle(train_ind)
    np.random.shuffle(test_ind)
    X_train, Y_train, X_vali, Y_vali = X[train_ind], Y[train_ind], X[test_ind], Y[test_ind]

    # Adjust Sample weights
    classimg = np.argmax(Y_train, axis=-1).flatten()
    n_zeros = len(classimg[classimg==0])
    n_ones = len(classimg[classimg==1])
    classweights = {0: 1, 1: n_zeros/n_ones}
    print(classweights)

    # Which model?
    if model is None:
        model = get_unet()

    # How to optimize?
    # sgd = SGD(lr=0.01, decay=1e-6, momentum=0.3, nesterov=False)
    # model.compile(optimizer='sgd',loss='categorical_crossentropy',metrics=['accuracy'])
    # model.compile(optimizer='sgd',loss=my_categorical_crossentropy((1,30.)),metrics=['accuracy'])
    # model.compile(optimizer=Adam(lr = 0.001),loss=my_categorical_crossentropy((1,10.)),metrics=['mean_squared_error'])
    cw = classweights
    model.compile(optimizer=Adam(lr = 0.00005), loss=my_categorical_crossentropy(weights=(cw[0], cw[1])), metrics=['accuracy'])

    # Callbacks
    # TODO: IO/Filepaths controlled by single module...
    checkpointer = ModelCheckpoint(filepath="./unet_model_weights_checkpoint.h5", verbose=0, save_best_only=True, save_weights_only=True)
    earlystopper = EarlyStopping(patience=patience, verbose=0)
    callbacks = [checkpointer, earlystopper]

    # Build and Train
    model.fit(X_train,
              Y_train,
              batch_size=batch_size,
              nb_epoch=nb_epoch,
              verbose=1,
              validation_data=(X_vali, Y_vali),
              callbacks=callbacks)

    score = model.evaluate(X_vali, Y_vali, verbose=0)
    print('Test score:', score[0])
    print('Test accuracy:', score[1])
    return model

# use the model for prediction

def predict_single_image(model, img):
    # predict on an image and save result
    X = imglist_to_X([img])
    Y_pred = model.predict(X)

    # n,one,pixels,classes = Y_pred.shape
    # Y_pred = np.argmax(Y_pred, axis=-1)

    # TODO: fix `2` below -> nclasses
    Y_pred = Y_pred.reshape((Y_pred.shape[0], y_width, x_width, 2))
    # WARNING: TODO: This will break when we change the coords used in `imglist_to_X`
    coords = regular_patch_coords(img)
    res = rebuild_img_from_patch_activations(img.shape, Y_pred, coords)
    return res[:,:,1].astype(np.float32)

def save_patches(X,Y,Ypred):
    import matplotlib.pyplot as plt
    def imsho(x, fname):
        # plt.figure()
        # plt.imshow(x, interpolation='nearest')
        io.imsave(fname, x)
    idx = np.random.randint(Ypred.shape[0])
    x= X[idx,0]
    a,b = x.shape
    # imsho(x, 'x.tif')
    y_gt = Y[idx,:,0].reshape((a,b))
    # imsho(y_gt)
    y_pre = Ypred[idx,:,0].reshape((a,b))
    # imsho(y_pre)
    io.imsave('randstack.tif', np.stack((x,y_gt,y_pre), axis=0))