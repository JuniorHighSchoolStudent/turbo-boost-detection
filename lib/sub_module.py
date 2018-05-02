import math
import torch
import torch.nn as nn
import torch.nn.functional as F
from lib.layers import pyramid_roi_align
from lib.roialign.roi_align.crop_and_resize import CropAndResizeFunction
import tools.utils as utils
from torch.autograd import Variable


class SamePad2d(nn.Module):
    """Mimic tensorflow's 'SAME' padding."""
    def __init__(self, kernel_size, stride):
        super(SamePad2d, self).__init__()
        self.kernel_size = torch.nn.modules.utils._pair(kernel_size)
        self.stride = torch.nn.modules.utils._pair(stride)

    def forward(self, input):
        in_width = input.size()[2]
        in_height = input.size()[3]
        out_width = math.ceil(float(in_width) / float(self.stride[0]))
        out_height = math.ceil(float(in_height) / float(self.stride[1]))
        pad_along_width = ((out_width - 1) * self.stride[0] +
                           self.kernel_size[0] - in_width)
        pad_along_height = ((out_height - 1) * self.stride[1] +
                            self.kernel_size[1] - in_height)
        pad_left = math.floor(pad_along_width / 2)
        pad_top = math.floor(pad_along_height / 2)
        pad_right = pad_along_width - pad_left
        pad_bottom = pad_along_height - pad_top
        return F.pad(input, (pad_left, pad_right, pad_top, pad_bottom), 'constant', 0)

    def __repr__(self):
        return self.__class__.__name__


############################################################
#  Resnet Graph
############################################################
class Bottleneck(nn.Module):
    expansion = 4

    def __init__(self, inplanes, planes, stride=1, downsample=None):
        super(Bottleneck, self).__init__()
        self.conv1 = nn.Conv2d(inplanes, planes, kernel_size=1, stride=stride)
        self.bn1 = nn.BatchNorm2d(planes, eps=0.001, momentum=0.01)
        self.padding2 = SamePad2d(kernel_size=3, stride=1)
        self.conv2 = nn.Conv2d(planes, planes, kernel_size=3)
        self.bn2 = nn.BatchNorm2d(planes, eps=0.001, momentum=0.01)
        self.conv3 = nn.Conv2d(planes, planes * 4, kernel_size=1)
        self.bn3 = nn.BatchNorm2d(planes * 4, eps=0.001, momentum=0.01)
        self.relu = nn.ReLU(inplace=True)
        self.downsample = downsample
        self.stride = stride

    def forward(self, x):
        residual = x

        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)

        out = self.padding2(out)
        out = self.conv2(out)
        out = self.bn2(out)
        out = self.relu(out)

        out = self.conv3(out)
        out = self.bn3(out)

        if self.downsample is not None:
            residual = self.downsample(x)

        out += residual
        out = self.relu(out)

        return out


class ResNet(nn.Module):

    def __init__(self, architecture, stage5=False):
        super(ResNet, self).__init__()
        assert architecture in ["resnet50", "resnet101"]
        self.inplanes = 64
        self.layers = [3, 4, {"resnet50": 6, "resnet101": 23}[architecture], 3]
        self.block = Bottleneck
        self.stage5 = stage5

        self.C1 = nn.Sequential(
            nn.Conv2d(3, 64, kernel_size=7, stride=2, padding=3),
            nn.BatchNorm2d(64, eps=0.001, momentum=0.01),
            nn.ReLU(inplace=True),
            SamePad2d(kernel_size=3, stride=2),
            nn.MaxPool2d(kernel_size=3, stride=2),
        )
        self.C2 = self.make_layer(self.block, 64, self.layers[0])
        self.C3 = self.make_layer(self.block, 128, self.layers[1], stride=2)
        self.C4 = self.make_layer(self.block, 256, self.layers[2], stride=2)
        if self.stage5:
            self.C5 = self.make_layer(self.block, 512, self.layers[3], stride=2)
        else:
            self.C5 = None

    def forward(self, x):
        x = self.C1(x)
        x = self.C2(x)
        x = self.C3(x)
        x = self.C4(x)
        x = self.C5(x)
        return x

    def stages(self):
        return [self.C1, self.C2, self.C3, self.C4, self.C5]

    def make_layer(self, block, planes, blocks, stride=1):
        downsample = None
        if stride != 1 or self.inplanes != planes * block.expansion:
            downsample = nn.Sequential(
                nn.Conv2d(self.inplanes, planes * block.expansion,
                          kernel_size=1, stride=stride),
                nn.BatchNorm2d(planes * block.expansion, eps=0.001, momentum=0.01),
            )
        layers = []
        layers.append(block(self.inplanes, planes, stride, downsample))
        self.inplanes = planes * block.expansion
        for i in range(1, blocks):
            layers.append(block(self.inplanes, planes))

        return nn.Sequential(*layers)


############################################################
#  FPN Graph
############################################################
# not used
# class TopDownLayer(nn.Module):
#
#     def __init__(self, in_channels, out_channels):
#         super(TopDownLayer, self).__init__()
#         self.conv1 = nn.Conv2d(in_channels, out_channels, kernel_size=1, stride=1)
#         self.padding2 = SamePad2d(kernel_size=3, stride=1)
#         self.conv2 = nn.Conv2d(out_channels, out_channels, kernel_size=3, stride=1)
#
#     def forward(self, x, y):
#         y = F.upsample(y, scale_factor=2)
#         x = self.conv1(x)
#         return self.conv2(self.padding2(x+y))
class FPN(nn.Module):
    def __init__(self, C1, C2, C3, C4, C5, out_channels):
        super(FPN, self).__init__()
        self.out_channels = out_channels
        self.C1 = C1
        self.C2 = C2
        self.C3 = C3
        self.C4 = C4
        self.C5 = C5
        self.P6 = nn.MaxPool2d(kernel_size=1, stride=2)
        self.P5_conv1 = nn.Conv2d(2048, self.out_channels, kernel_size=1, stride=1)
        self.P5_conv2 = nn.Sequential(
            SamePad2d(kernel_size=3, stride=1),
            nn.Conv2d(self.out_channels, self.out_channels, kernel_size=3, stride=1),
        )
        self.P4_conv1 = nn.Conv2d(1024, self.out_channels, kernel_size=1, stride=1)
        self.P4_conv2 = nn.Sequential(
            SamePad2d(kernel_size=3, stride=1),
            nn.Conv2d(self.out_channels, self.out_channels, kernel_size=3, stride=1),
        )
        self.P3_conv1 = nn.Conv2d(512, self.out_channels, kernel_size=1, stride=1)
        self.P3_conv2 = nn.Sequential(
            SamePad2d(kernel_size=3, stride=1),
            nn.Conv2d(self.out_channels, self.out_channels, kernel_size=3, stride=1),
        )
        self.P2_conv1 = nn.Conv2d(256, self.out_channels, kernel_size=1, stride=1)
        self.P2_conv2 = nn.Sequential(
            SamePad2d(kernel_size=3, stride=1),
            nn.Conv2d(self.out_channels, self.out_channels, kernel_size=3, stride=1),
        )

    def forward(self, x):
        x = self.C1(x)
        x = self.C2(x)
        c2_out = x
        x = self.C3(x)
        c3_out = x
        x = self.C4(x)
        c4_out = x
        x = self.C5(x)
        p5_out = self.P5_conv1(x)
        p4_out = self.P4_conv1(c4_out) + F.upsample(p5_out, scale_factor=2)
        p3_out = self.P3_conv1(c3_out) + F.upsample(p4_out, scale_factor=2)
        p2_out = self.P2_conv1(c2_out) + F.upsample(p3_out, scale_factor=2)

        p5_out = self.P5_conv2(p5_out)
        p4_out = self.P4_conv2(p4_out)
        p3_out = self.P3_conv2(p3_out)
        p2_out = self.P2_conv2(p2_out)

        # P6 is used for the 5th anchor scale in RPN. Generated by
        # subsampling from P5 with stride of 2.
        p6_out = self.P6(p5_out)

        return [p2_out, p3_out, p4_out, p5_out, p6_out]


############################################################
#  Region Proposal Network
############################################################
class RPN(nn.Module):
    """Builds the model of Region Proposal Network.
    anchors_per_location: number of anchors per pixel in the feature map
    anchor_stride: Controls the density of anchors. Typically 1 (anchors for
                   every pixel in the feature map), or 2 (every other pixel).
    Returns:
        rpn_logits: [batch, H, W, 2] Anchor classifier logits (before softmax)
        rpn_probs: [batch, W, W, 2] Anchor classifier probabilities.
        rpn_bbox: [batch, H, W, (dy, dx, log(dh), log(dw))] Deltas to be applied to anchors.
    """
    # TODO (low): check if RPN is very shallow with original paper;
    # TODO (mid): or change conv_shared to separate ones across scales
    def __init__(self, anchors_per_location, anchor_stride, input_ch):
        super(RPN, self).__init__()
        self.anchor_stride = anchor_stride
        self.input_ch = input_ch

        self.padding = SamePad2d(kernel_size=3, stride=self.anchor_stride)
        self.conv_shared = nn.Conv2d(self.input_ch, 512, kernel_size=3, stride=self.anchor_stride)
        self.relu = nn.ReLU(inplace=True)
        self.conv_class = nn.Conv2d(512, 2 * anchors_per_location, kernel_size=1, stride=1)
        self.softmax = nn.Softmax(dim=2)
        self.conv_bbox = nn.Conv2d(512, 4 * anchors_per_location, kernel_size=1, stride=1)

    def forward(self, x):
        # Shared convolutional base of the RPN
        x = self.relu(self.conv_shared(self.padding(x)))

        # Anchor Score. [batch, anchors per location * 2, height, width].
        rpn_class_logits = self.conv_class(x)

        # Reshape to [batch, 2, anchors]
        rpn_class_logits = rpn_class_logits.permute(0, 2, 3, 1)
        rpn_class_logits = rpn_class_logits.contiguous()
        rpn_class_logits = rpn_class_logits.view(x.size()[0], -1, 2)

        # Softmax on last dimension of BG/FG.
        rpn_probs = self.softmax(rpn_class_logits)

        # Bounding box refinement. [batch, H, W, anchors per location, depth]
        # where depth is [x, y, log(w), log(h)]
        rpn_bbox = self.conv_bbox(x)

        # Reshape to [batch, 4, anchors]
        rpn_bbox = rpn_bbox.permute(0, 2, 3, 1)
        rpn_bbox = rpn_bbox.contiguous()
        rpn_bbox = rpn_bbox.view(x.size()[0], -1, 4)

        return [rpn_class_logits, rpn_probs, rpn_bbox]


############################################################
#  DEV
############################################################
class Dev(nn.Module):
    def __init__(self, config, depth):
        super(Dev, self).__init__()
        self.depth = depth
        self.use_dev = config.DEV.SWITCH
        self.pool_size = config.MRCNN.POOL_SIZE
        self.mask_pool_size = config.MRCNN.MASK_POOL_SIZE
        self.image_shape = config.DATA.IMAGE_SHAPE

        if self.use_dev:

            # TODO: for now it's the same size with mask_pool_size (14)
            self.feat_pool_size = config.DEV.FEAT_BRANCH_POOL_SIZE
            self.upsample_fac = config.DEV.UPSAMPLE_FAC
            assert self.feat_pool_size % 2 == 0, 'pool size of feature branch has to be even'

            # define upsample
            if self.upsample_fac == 2.0:
                self.upsample = nn.Sequential(*[
                    nn.ConvTranspose2d(self.depth, self.depth, kernel_size=3, stride=2, padding=1, output_padding=1),
                    nn.BatchNorm2d(self.depth),
                    nn.ReLU(inplace=True),
                    nn.Conv2d(self.depth, self.depth, kernel_size=3, stride=1, padding=1),
                    nn.BatchNorm2d(self.depth),
                    nn.ReLU(inplace=True)
                ])
            else:
                raise Exception('unsupported upsampling factor')

            # define feature extractor to be compared
            _ksize = int(self.feat_pool_size / 2)
            self.feat_extract = nn.Sequential(*[
                nn.Conv2d(self.depth, 512, kernel_size=3, padding=1, stride=2),   # halve the map
                nn.BatchNorm2d(512),
                nn.ReLU(inplace=True),
                nn.Conv2d(512, 1024, kernel_size=_ksize, stride=1),
                nn.BatchNorm2d(1024),
                nn.ReLU(inplace=True),
            ])

    def _find_big_box(self, level, roi_level):
        if level == 2:
            big_ix = (roi_level == 4) + (roi_level == 5)
        elif level == 3:
            big_ix = (roi_level == 5)
        else:
            # TODO: think this in detail
            # for now, higher scale 4, 5 do not apply the new rule
            big_ix = (roi_level == -1)
        return big_ix

    def forward(self, x, rois, roi_cls_gt=None):
        # x is a multi-scale List containing Variable
        if not self.use_dev:
            # in 'layers.py'
            pooled_out = pyramid_roi_align([rois] + x, self.pool_size, self.image_shape)
            mask_out = pyramid_roi_align([rois] + x, self.mask_pool_size, self.image_shape)
            feat_out = None
        else:
            # Assign each ROI to a level in the pyramid based on the ROI area.
            y1, x1, y2, x2 = rois.chunk(4, dim=2)
            h, w = y2 - y1, x2 - x1
            image_area = Variable(torch.FloatTensor(
                [float(self.image_shape[0]*self.image_shape[1])]), requires_grad=False).cuda()
            roi_level = 4 + utils.log2(torch.sqrt(h*w)/(224.0/torch.sqrt(image_area)))
            roi_level = roi_level.round().int()
            # in case batch size =1, we keep that dim
            roi_level = roi_level.clamp(2, 5).squeeze(dim=-1)   # size: [bs, num_roi], say [3, 200]

            pooled, mask, box_to_level = [], [], []
            feat_out = []
            # Loop through levels and apply ROI pooling to each. P2 to P5.
            # with 2 being the most coarse map
            for i, level in enumerate(range(2, 6)):

                # ix: bs, num_roi
                ix = roi_level == level
                if not ix.any():
                    # there is no "small" boxes
                    continue

                curr_feat_maps = x[i]
                _use_upsample = True if level in [2, 3] else False

                big_ix = self._find_big_box(level, roi_level)
                if not big_ix.any():
                    # there is no "big" boxes
                    big_box_gt, big_output = [], []   # never mind; we use historic data
                else:
                    big_index = torch.nonzero(big_ix)
                    big_boxes = rois[big_index[:, 0].data, big_index[:, 1].data, :]
                    big_box_gt = roi_cls_gt[big_index[:, 0].data, big_index[:, 1].data]
                    # for big boxes, ROI-pool on original map
                    big_box_ind = big_index[:, 0].int()
                    # shape: say 20, 256, 14, 14
                    big_feat = CropAndResizeFunction(self.feat_pool_size,
                                                     self.feat_pool_size)(curr_feat_maps, big_boxes, big_box_ind)
                    # shape: say 20, 1024
                    big_output = self.feat_extract(big_feat).squeeze()

                # "SMALL" boxes (or simply boxes on scale 4,5) exist
                # index: say, 2670 (actual boxes found in this level) x 2
                index = torch.nonzero(ix)
                # Keep track of which box is mapped to which level
                box_to_level.append(index.data)
                # rois: [bs, num_roi, 4] -> small_boxes [index[0], 4]
                small_boxes = rois[index[:, 0].data, index[:, 1].data, :]
                small_box_gt = roi_cls_gt[index[:, 0].data, index[:, 1].data]

                # scale up feature map of smaller boxes
                box_ind = index[:, 0].int()
                if _use_upsample:
                    small_boxes *= self.upsample_fac
                    _feat_maps = self.upsample(curr_feat_maps)
                else:
                    _feat_maps = curr_feat_maps

                # shape: say 473, 256, 7, 7
                pooled_features = CropAndResizeFunction(self.pool_size,
                                                        self.pool_size)(_feat_maps, small_boxes, box_ind)
                pooled.append(pooled_features)

                # mask and feat features are shared with a RoI since the output size is the same
                # (mask_pool_size=feat_pool_size)
                # shape: say 473, 256, 14, 14
                mask_and_feat = CropAndResizeFunction(self.mask_pool_size,
                                                      self.mask_pool_size)(_feat_maps, small_boxes, box_ind)
                mask.append(mask_and_feat)

                # for scale 4 and 5, we don't do meta-supervise
                if _use_upsample:
                    # process big-small-supervise
                    small_output = self.feat_extract(mask_and_feat).squeeze()
                    feat_out.append([big_box_gt, big_output, small_box_gt, small_output])

            pooled_out, mask_out = self._reshape_result(pooled, mask, box_to_level, rois.size())

        return pooled_out, mask_out, feat_out

    def _reshape_result(self, pooled, mask, box_to_level, rois_size):
        pooled = torch.cat(pooled, dim=0)
        mask = torch.cat(mask, dim=0)
        box_to_level = torch.cat(box_to_level, dim=0)

        # Rearrange pooled features to match the order of the original boxes
        pooled_out = Variable(torch.zeros(
            rois_size[0], rois_size[1], pooled.size(1), pooled.size(2), pooled.size(3)).cuda())
        pooled_out[box_to_level[:, 0], box_to_level[:, 1], :, :, :] = pooled
        # 3, 1000, 256, 7, 7 -> 3000, 256, 7, 7
        pooled_out = pooled_out.view(-1, pooled_out.size(2), pooled_out.size(3), pooled_out.size(4))

        mask_out = Variable(torch.zeros(
            rois_size[0], rois_size[1], mask.size(1), mask.size(2), mask.size(3)).cuda())
        mask_out[box_to_level[:, 0], box_to_level[:, 1], :, :, :] = mask
        mask_out = mask_out.view(-1, mask_out.size(2), mask_out.size(3), mask_out.size(4))

        return pooled_out, mask_out


############################################################
#  Feature Pyramid Network Heads
############################################################
class Classifier(nn.Module):
    def __init__(self, depth, num_classes, pool_size):
        super(Classifier, self).__init__()
        self.depth = depth
        self.pool_size = pool_size
        self.num_classes = num_classes
        self.conv1 = nn.Conv2d(self.depth, 1024, kernel_size=self.pool_size, stride=1)
        self.bn1 = nn.BatchNorm2d(1024, eps=0.001, momentum=0.01)
        self.conv2 = nn.Conv2d(1024, 1024, kernel_size=1, stride=1)
        self.bn2 = nn.BatchNorm2d(1024, eps=0.001, momentum=0.01)
        self.relu = nn.ReLU(inplace=True)

        self.linear_class = nn.Linear(1024, num_classes)
        self.softmax = nn.Softmax(dim=1)

        self.linear_bbox = nn.Linear(1024, num_classes * 4)

    def forward(self, x):
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)
        x = self.conv2(x)
        x = self.bn2(x)
        x = self.relu(x)

        x = x.view(-1, 1024)
        mrcnn_class_logits = self.linear_class(x)
        mrcnn_probs = self.softmax(mrcnn_class_logits)

        mrcnn_bbox = self.linear_bbox(x)
        mrcnn_bbox = mrcnn_bbox.view(mrcnn_bbox.size()[0], -1, 4)

        return [mrcnn_class_logits, mrcnn_probs, mrcnn_bbox]


class Mask(nn.Module):
    def __init__(self, depth, num_classes):
        super(Mask, self).__init__()
        self.depth = depth
        self.num_classes = num_classes
        self.padding = SamePad2d(kernel_size=3, stride=1)
        self.conv1 = nn.Conv2d(self.depth, 256, kernel_size=3, stride=1)
        self.bn1 = nn.BatchNorm2d(256, eps=0.001)
        self.conv2 = nn.Conv2d(256, 256, kernel_size=3, stride=1)
        self.bn2 = nn.BatchNorm2d(256, eps=0.001)
        self.conv3 = nn.Conv2d(256, 256, kernel_size=3, stride=1)
        self.bn3 = nn.BatchNorm2d(256, eps=0.001)
        self.conv4 = nn.Conv2d(256, 256, kernel_size=3, stride=1)
        self.bn4 = nn.BatchNorm2d(256, eps=0.001)
        self.deconv = nn.ConvTranspose2d(256, 256, kernel_size=2, stride=2)
        # TODO(check here): no bn after deconv
        self.conv5 = nn.Conv2d(256, num_classes, kernel_size=1, stride=1)
        self.sigmoid = nn.Sigmoid()
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x):
        x = self.conv1(self.padding(x))
        x = self.bn1(x)
        x = self.relu(x)
        x = self.conv2(self.padding(x))
        x = self.bn2(x)
        x = self.relu(x)
        x = self.conv3(self.padding(x))
        x = self.bn3(x)
        x = self.relu(x)
        x = self.conv4(self.padding(x))
        x = self.bn4(x)
        x = self.relu(x)
        x = self.deconv(x)
        x = self.relu(x)
        x = self.conv5(x)
        x = self.sigmoid(x)
        # output is 28 x 28; matches the mask_shape
        return x