import torch
from torch import nn
import torch.nn.functional as F


def sparse_init_weight(model):
    for m in model.modules():
        if isinstance(m, nn.Conv3d):
            torch.nn.init.kaiming_normal_(
                m.weight,
            )
        elif isinstance(m, nn.BatchNorm3d):
            m.weight.data.fill_(1)
            m.bias.data.zero_()
    return model


class ConvBlock(nn.Module):
    def __init__(self, n_stages, n_filters_in, n_filters_out, normalization="none"):
        super(ConvBlock, self).__init__()

        ops = []
        for i in range(n_stages):
            if i == 0:
                input_channel = n_filters_in
            else:
                input_channel = n_filters_out

            ops.append(nn.Conv3d(input_channel, n_filters_out, 3, padding=1))
            if normalization == "batchnorm":
                ops.append(nn.BatchNorm3d(n_filters_out))
            elif normalization == "groupnorm":
                ops.append(nn.GroupNorm(num_groups=16, num_channels=n_filters_out))
            elif normalization == "instancenorm":
                ops.append(nn.InstanceNorm3d(n_filters_out))
            elif normalization != "none":
                assert False
            ops.append(nn.ReLU(inplace=True))

        self.conv = nn.Sequential(*ops)

    def forward(self, x):
        x = self.conv(x)
        return x


class ResidualConvBlock(nn.Module):
    def __init__(self, n_stages, n_filters_in, n_filters_out, normalization="none"):
        super(ResidualConvBlock, self).__init__()

        ops = []
        for i in range(n_stages):
            if i == 0:
                input_channel = n_filters_in
            else:
                input_channel = n_filters_out

            ops.append(nn.Conv3d(input_channel, n_filters_out, 3, padding=1))
            if normalization == "batchnorm":
                ops.append(nn.BatchNorm3d(n_filters_out))
            elif normalization == "groupnorm":
                ops.append(nn.GroupNorm(num_groups=16, num_channels=n_filters_out))
            elif normalization == "instancenorm":
                ops.append(nn.InstanceNorm3d(n_filters_out))
            elif normalization != "none":
                assert False

            if i != n_stages - 1:
                ops.append(nn.ReLU(inplace=True))

        self.conv = nn.Sequential(*ops)
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x):
        x = self.conv(x) + x
        x = self.relu(x)
        return x


class DownsamplingConvBlock(nn.Module):
    def __init__(self, n_filters_in, n_filters_out, stride=2, normalization="none"):
        super(DownsamplingConvBlock, self).__init__()

        ops = []
        if normalization != "none":
            ops.append(
                nn.Conv3d(n_filters_in, n_filters_out, stride, padding=0, stride=stride)
            )
            if normalization == "batchnorm":
                ops.append(nn.BatchNorm3d(n_filters_out))
            elif normalization == "groupnorm":
                ops.append(nn.GroupNorm(num_groups=16, num_channels=n_filters_out))
            elif normalization == "instancenorm":
                ops.append(nn.InstanceNorm3d(n_filters_out))
            else:
                assert False
        else:
            ops.append(
                nn.Conv3d(n_filters_in, n_filters_out, stride, padding=0, stride=stride)
            )

        ops.append(nn.ReLU(inplace=True))

        self.conv = nn.Sequential(*ops)

    def forward(self, x):
        x = self.conv(x)
        return x


class UpsamplingDeconvBlock(nn.Module):
    def __init__(self, n_filters_in, n_filters_out, stride=2, normalization="none"):
        super(UpsamplingDeconvBlock, self).__init__()

        ops = []
        if normalization != "none":
            ops.append(
                nn.ConvTranspose3d(
                    n_filters_in, n_filters_out, stride, padding=0, stride=stride
                )
            )
            if normalization == "batchnorm":
                ops.append(nn.BatchNorm3d(n_filters_out))
            elif normalization == "groupnorm":
                ops.append(nn.GroupNorm(num_groups=16, num_channels=n_filters_out))
            elif normalization == "instancenorm":
                ops.append(nn.InstanceNorm3d(n_filters_out))
            else:
                assert False
        else:
            ops.append(
                nn.ConvTranspose3d(
                    n_filters_in, n_filters_out, stride, padding=0, stride=stride
                )
            )

        ops.append(nn.ReLU(inplace=True))

        self.conv = nn.Sequential(*ops)

    def forward(self, x):
        x = self.conv(x)
        return x


class Upsampling(nn.Module):
    def __init__(self, n_filters_in, n_filters_out, stride=2, normalization="none"):
        super(Upsampling, self).__init__()

        ops = []
        ops.append(
            nn.Upsample(scale_factor=stride, mode="trilinear", align_corners=False)
        )
        ops.append(nn.Conv3d(n_filters_in, n_filters_out, kernel_size=3, padding=1))
        if normalization == "batchnorm":
            ops.append(nn.BatchNorm3d(n_filters_out))
        elif normalization == "groupnorm":
            ops.append(nn.GroupNorm(num_groups=16, num_channels=n_filters_out))
        elif normalization == "instancenorm":
            ops.append(nn.InstanceNorm3d(n_filters_out))
        elif normalization != "none":
            assert False
        ops.append(nn.ReLU(inplace=True))

        self.conv = nn.Sequential(*ops)

    def forward(self, x):
        x = self.conv(x)
        return x


class VNet(nn.Module):
    def __init__(
        self,
        n_channels=3,
        n_classes=2,
        n_filters=16,
        normalization="none",
        has_dropout=False,
        pert_gap=0.5,
        pert_type="dropout",
    ):
        super(VNet, self).__init__()
        self.has_dropout = has_dropout

        self.pert = PertDropout(p=pert_gap, type=pert_type)

        self.block_one = ConvBlock(
            1, n_channels, n_filters, normalization=normalization
        )
        self.block_one_dw = DownsamplingConvBlock(
            n_filters, 2 * n_filters, normalization=normalization
        )

        self.block_two = ConvBlock(
            2, n_filters * 2, n_filters * 2, normalization=normalization
        )
        self.block_two_dw = DownsamplingConvBlock(
            n_filters * 2, n_filters * 4, normalization=normalization
        )

        self.block_three = ConvBlock(
            3, n_filters * 4, n_filters * 4, normalization=normalization
        )
        self.block_three_dw = DownsamplingConvBlock(
            n_filters * 4, n_filters * 8, normalization=normalization
        )

        self.block_four = ConvBlock(
            3, n_filters * 8, n_filters * 8, normalization=normalization
        )
        self.block_four_dw = DownsamplingConvBlock(
            n_filters * 8, n_filters * 16, normalization=normalization
        )

        self.block_five = ConvBlock(
            3, n_filters * 16, n_filters * 16, normalization=normalization
        )
        self.block_five_up = UpsamplingDeconvBlock(
            n_filters * 16, n_filters * 8, normalization=normalization
        )

        self.block_six = ConvBlock(
            3, n_filters * 8, n_filters * 8, normalization=normalization
        )
        self.block_six_up = UpsamplingDeconvBlock(
            n_filters * 8, n_filters * 4, normalization=normalization
        )

        self.block_seven = ConvBlock(
            3, n_filters * 4, n_filters * 4, normalization=normalization
        )
        self.block_seven_up = UpsamplingDeconvBlock(
            n_filters * 4, n_filters * 2, normalization=normalization
        )

        self.block_eight = ConvBlock(
            2, n_filters * 2, n_filters * 2, normalization=normalization
        )
        self.block_eight_up = UpsamplingDeconvBlock(
            n_filters * 2, n_filters, normalization=normalization
        )

        self.block_nine = ConvBlock(
            1, n_filters, n_filters, normalization=normalization
        )
        self.out_conv = nn.Conv3d(n_filters, n_classes, 1, padding=0)

        self.dropout = nn.Dropout3d(p=0.5, inplace=False)

        # sparse_init_weight(self)

    def encoder(self, input):
        x1 = self.block_one(input)
        x1_dw = self.block_one_dw(x1)

        x2 = self.block_two(x1_dw)
        x2_dw = self.block_two_dw(x2)

        x3 = self.block_three(x2_dw)
        x3_dw = self.block_three_dw(x3)

        x4 = self.block_four(x3_dw)
        x4_dw = self.block_four_dw(x4)

        x5 = self.block_five(x4_dw)
        # x5 = F.dropout3d(x5, p=0.5, training=True)
        if self.has_dropout:
            x5 = self.dropout(x5)

        res = [x1, x2, x3, x4, x5]

        return res

    def decoder(self, features, no_drop=False):
        x1 = features[0]
        x2 = features[1]
        x3 = features[2]
        x4 = features[3]
        x5 = features[4]

        x5_up = self.block_five_up(x5)
        x5_up = x5_up + x4

        x6 = self.block_six(x5_up)
        x6_up = self.block_six_up(x6)
        x6_up = x6_up + x3

        x7 = self.block_seven(x6_up)
        x7_up = self.block_seven_up(x7)
        x7_up = x7_up + x2

        x8 = self.block_eight(x7_up)
        x8_up = self.block_eight_up(x8)
        x8_up = x8_up + x1
        x9 = self.block_nine(x8_up)
        # x9 = F.dropout3d(x9, p=0.5, training=True)
        if not no_drop:
            if self.has_dropout:
                x9 = self.dropout(x9)
        out = self.out_conv(x9)
        return out

    def forward(self, input, turnoff_drop=False, need_fp=False, input_type="normal"):
        if turnoff_drop:
            has_dropout = self.has_dropout
            self.has_dropout = False
        features = self.encoder(input)

        if need_fp:
            if input_type == "normal":
                # "normal"模式：对特征应用Dropout并拼接
                features_perturbed = [self.dropout(feat) for feat in features]
                combined_features = [torch.cat((feat, feat_pert), dim=0) for feat, feat_pert in zip(features, features_perturbed)]
                outs = self.decoder(combined_features, no_drop=True)
                return outs.chunk(2)
            
            elif input_type == "strong":
                # "strong"模式：生成互补的弱和强Dropout特征
                p_keep = 0.7  # 弱分支的保留概率
                features1 = []
                features2 = []
                for feats in features:
                    f1, f2 = feats.chunk(2)
                    features1.append(f1)
                    features2.append(f2)
                
                weak_features = []
                strong_features = []
                complete_features = []
                for f1, f2 in zip(features1, features2):
                    mask = (torch.rand_like(f1) < p_keep).float()
                    weak_f = f1 * mask
                    strong_f = f2 * (1 - mask)
                    complete_f = weak_f + strong_f
                    weak_features.append(weak_f)
                    strong_features.append(strong_f)
                    complete_features.append(complete_f)
                
                # 解码生成多个预测
                pred_u_s1_clean = self.decoder(features1)
                pred_u_s2_clean = self.decoder(features2)
                pred_u_s1_weak = self.decoder(weak_features)
                pred_u_s2_strong = self.decoder(strong_features)
                pred_complete = self.decoder(complete_features)
                
                return pred_u_s1_clean, pred_u_s2_clean, pred_u_s1_weak, pred_u_s2_strong, pred_complete

        out = self.decoder(features)
        if turnoff_drop:
            self.has_dropout = has_dropout
        return out


class PertDropout(nn.Module):
    def __init__(self, p=0.5, type="dropout"):
        super(PertDropout, self).__init__()
        self.p = p
        top = 0.5 + p / 2
        bottom = 0.5 - p / 2
        print("-" * 25, f"Info: Using 3D dropout with {top}~{bottom}", "-" * 25)
        print("-" * 25, f"Info: Using {type} dropout", "-" * 25)

        dropout_type = {
            "dropout": nn.Dropout3d,
            "alpha": nn.AlphaDropout,
            "feature": nn.FeatureAlphaDropout,
        }

        self.dropouts = [
            dropout_type[type](bottom).cuda(),  # Weak
            dropout_type[type](top).cuda(),  # Strong
        ]

        self.len = len(self.dropouts)

    def __len__(self):
        return self.len

    def forward(self, x):
        rst = []
        for pert_dropout in self.dropouts:
            single_type = []
            for i, feat in enumerate(x):
                perted = pert_dropout(feat)
                single_type.append(perted)
            rst.append(single_type)
        return rst


if __name__ == "__main__":
    from thop import profile, clever_format

    model = VNet(n_channels=1, n_classes=2)
    input = torch.randn(4, 1, 112, 112, 80)
    # 测试"strong"模式
    outputs = model(input, need_fp=True, input_type="normal")
    for i, out in enumerate(outputs):
        print(f"Output {i} shape: {out.shape}")