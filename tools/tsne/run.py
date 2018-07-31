import numpy as np
import os
import torch
import torch.optim as optim
import random

from sklearn import manifold, datasets
from sklearn.metrics.pairwise import pairwise_distances
from scipy.spatial.distance import squareform

import matplotlib
matplotlib.use('Agg')
from matplotlib.patches import Ellipse
import matplotlib.pyplot as plt
from tools.tsne.fetch_data import prepare_feature


def preprocess(perplexity=30, metric='euclidean', data=None):
    """ Compute pairiwse probabilities for MNIST pixels.
    """
    if data is None:
        digits = datasets.load_digits(n_class=6)
        pos = digits.data           # ndarray: 1083 (sample index) x 64 (feat)
        y = digits.target           # ndarray: (1083, ), labels
        n_points = pos.shape[0]
    else:
        pos = data[0]
        y = data[1]
        n_points = len(y)

    # TODO: transfer to GPU tensor
    distances2 = pairwise_distances(pos, metric=metric, squared=True)
    # This return a n x (n-1) prob array
    pij = manifold.t_sne._joint_probabilities(distances2, perplexity, False)
    # Convert to n x n prob array
    pij2 = squareform(pij)

    i, j = np.indices(pij2.shape)
    i, j = i.ravel(), j.ravel()
    out_p_ij = pij2.ravel().astype('float32')
    # remove self-indices
    idx = i != j
    i, j, out_p_ij = i[idx], j[idx], out_p_ij[idx]
    return n_points, pij, i, j, y


def chunks(n, *args):
    """Yield successive n-sized chunks from l."""
    endpoints = []
    start = 0
    for stop in range(0, len(args[0]), n):
        if stop - start > 0:
            endpoints.append((start, stop))
            start = stop
    random.shuffle(endpoints)
    for start, stop in endpoints:
        yield [a[start: stop] for a in args]


# PARAMS
use_v = True
draw_ellipse = True
n_topics = 2
total_ep = 500
batch_size = 1024

# PREPARE DATA
choice = 'first'
data = prepare_feature(choice)
n_points, pij, i, j, y = preprocess(data)
print('use_v {}; batch_sizez {}; sample number {}; topic {}'.format(use_v, batch_size, n_points, n_topics))

device = 'cuda' if torch.cuda.is_available() else 'cpu'
# CREATE MODEL
if use_v:
    from tools.tsne.vtsne import VTSNE
    model = VTSNE(n_points, n_topics, device)
    result_folder = os.path.join('results', choice)
else:
    raise NotImplementedError
    # from tools.tsne.tsne import TSNE
    # model = TSNE(n_points, n_points)
    # result_folder = 'res_not_v'
model = model.to(device)
optimizer = optim.Adam(model.parameters(), lr=1e-2)

if not os.path.exists(result_folder):
    os.makedirs(result_folder)

# PIPELINE
for epoch in range(total_ep):

    model.train()
    # ONE EPOCH BELOW
    total = 0.0
    for itr, datas in enumerate(chunks(batch_size, pij, i, j)):

        datas = [torch.from_numpy(data).to(device) for data in datas]
        optimizer.zero_grad()
        loss = model(*datas)

        # import pdb
        # pdb.set_trace()
        loss.backward()
        optimizer.step()
        total += loss.item()

    if epoch % 25 == 0:
        print('Train epoch: {} \tLoss: {:.6e}'.format(epoch, total / (len(pij) * 1.0)))

    # TODO: Visualize the results
    embed = model.logits.weight.cpu().data.numpy()
    f = plt.figure()

    if draw_ellipse:
        # Visualize with ellipses
        var = np.sqrt(model.logits_lv.weight.clone().exp_().cpu().data.numpy())
        ax = plt.gca()
        for xy, (w, h), c in zip(embed, var, y):
            e = Ellipse(xy=xy, width=w, height=h, ec=None, lw=0.0)
            e.set_facecolor(plt.cm.Paired(c * 1.0 / y.max()))
            e.set_alpha(0.5)
            ax.add_artist(e)
        ax.set_xlim(-9, 9)
        ax.set_ylim(-9, 9)
        plt.axis('off')
        plt.savefig(os.path.join(result_folder, 'scatter_{:03d}.png'.format(epoch)), bbox_inches='tight')
        plt.close(f)

    else:
        plt.scatter(embed[:, 0], embed[:, 1], c=y * 1.0 / y.max())
        plt.axis('off')
        plt.savefig(os.path.join(result_folder, 'scatter_{:03d}.png'.format(epoch)), bbox_inches='tight')
        plt.close(f)

print('Done!')
