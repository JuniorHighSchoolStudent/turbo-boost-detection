import torch
import os

# in terminal, execute python this_file_name.py, the following path is right; however, in MacPycharm, it sees tools folder as root
base_name = 'results/base_102/train'
file_name = 'mask_rcnn_ep_0006_iter_003660.pth'
# old file
model_path = os.path.join(base_name, file_name)
# load model
checkpoints = torch.load(model_path)
weights = checkpoints['state_dict']
iter = checkpoints['iter']
epoch = checkpoints['epoch'] + 1

# new file
model_file = os.path.join(base_name, 'mask_rcnn_ep_{:04d}_iter_{:06d}.pth'.format(epoch, iter))
print('saving file: {}'.format(model_file))
torch.save({
    'state_dict':   weights,
    'epoch':        epoch,
    'iter':         iter,
}, model_file)
print('removing old file: {}'.format(model_path))
os.remove(model_path)



