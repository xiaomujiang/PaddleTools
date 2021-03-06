import os
import warnings

import numpy as np
import paddle.fluid as fluid
import torch
import torch.nn.functional as F
from paddle.fluid.dygraph import BatchNorm, Conv2D, Linear
from paddletools import logger
from paddletools.checkpoints import (dynamic2static, static2dynamic,
                                     torch2dynamic)
from torch import nn
from torch.nn import BatchNorm2d, Conv2d, ReLU

place = fluid.CPUPlace()
logger.log_to_file("test.log")
warnings.filterwarnings(action="ignore")


def reader():
    return np.ones((1, 3, 16, 16), dtype=np.float32)


def build_static_network(save_params=False, load_pretrain=None):
    main_prog = fluid.Program()
    startup_prog = fluid.Program()
    exe = fluid.Executor(place)
    with fluid.program_guard(main_prog, startup_prog):
        data = fluid.layers.data(name="img", shape=[1, 3, 16, 16], append_batch_size=False)
        conv = fluid.layers.conv2d(
            input=data, num_filters=16, filter_size=3,
            param_attr=fluid.ParamAttr(name='conv.weight'),
            bias_attr=fluid.ParamAttr(name='conv.bias'))
        bn = fluid.layers.batch_norm(
            input=conv, act="relu",
            param_attr=fluid.ParamAttr(name='bn.scale'),
            bias_attr=fluid.ParamAttr(name='bn.offset'),
            moving_mean_name='bn.mean',
            moving_variance_name='bn.variance')
        batch_size = bn.shape[0]
        f = fluid.layers.reshape(bn, [batch_size, -1])
        fc = fluid.layers.fc(
            input=f, size=3,
            param_attr=fluid.ParamAttr(name='fc.w_0'),
            bias_attr=fluid.ParamAttr(name='fc.b_0'))
        logits = fluid.layers.softmax(fc)
    eval_prog = main_prog.clone(True)
    exe.run(startup_prog)

    if load_pretrain:
        fluid.io.load_persistables(exe, load_pretrain, main_prog)

    d = {"img": reader()}
    result = exe.run(eval_prog, feed=d, fetch_list=[logits.name])
    logger.info(result[0])
    if save_params:
        if not os.path.exists("params"):
            os.mkdir("params")
        fluid.io.save_persistables(exe, "params", main_prog)


class TestModel(fluid.dygraph.Layer):

    def __init__(self, name):
        super(TestModel, self).__init__(name)
        self.conv = Conv2D(3, 16, filter_size=3,
                           param_attr=fluid.ParamAttr(name='conv.weight'),
                           bias_attr=fluid.ParamAttr(name='conv.bias'))
        self.bn = BatchNorm(16, act="relu",
                            param_attr=fluid.ParamAttr(name="bn.scale"),
                            bias_attr=fluid.ParamAttr(name="bn.offset"),
                            moving_mean_name="bn.mean",
                            moving_variance_name="bn.variance")
        self.fc = Linear(3136, 3,
                         param_attr=fluid.ParamAttr(name='fc.weight'),
                         bias_attr=fluid.ParamAttr(name='fc.bias'))

    def forward(self, x):
        b = x.shape[0]
        x = self.conv(x)
        x = self.bn(x)
        x = fluid.layers.reshape(x, [b, -1])
        x = self.fc(x)
        x = fluid.layers.softmax(x)
        return x


def build_dynamic_network(load_params=None, save_params=False,
                          use_structured_name=False):
    with fluid.dygraph.guard(place):
        model = TestModel("test")
        if load_params:
            model_state_dict, _ = fluid.load_dygraph(load_params)
            model.load_dict(model_state_dict, use_structured_name=use_structured_name)
        model.eval()
        d = fluid.dygraph.to_variable(reader())
        p = model(d)
        logger.info(p.numpy())
        if save_params:
            fluid.save_dygraph(model.state_dict(), "dynamic_params")


class TorchModel(nn.Module):

    def __init__(self):
        super(TorchModel, self).__init__()
        self.conv = Conv2d(3, 16, 3)
        self.bn = BatchNorm2d(16)
        self.relu = ReLU(inplace=True)
        self.fc = nn.Linear(3136, 3)

    def forward(self, x):
        x = self.conv(x)
        x = self.bn(x)
        x = self.relu(x)
        x = x.view(x.size(0), -1)
        x = self.fc(x)
        return F.softmax(x)


def build_torch_model(save_params=True):
    model = TorchModel()
    model.eval()
    d = torch.from_numpy(reader())
    y = model(d)
    logger.info(y.tolist())
    if save_params:
        torch.save(model.state_dict(), "torch_model.params")


if __name__ == "__main__":
    logger.info(">>> build satic network & save params...")
    build_static_network(save_params=True)
    logger.info(">>> read static params & build dynamic network...")
    static2dynamic("params", "dynamic")
    build_dynamic_network(load_params="dynamic")

    print("\n<========================>\n")

    logger.info(">>> build dynamic network & save params...")
    build_dynamic_network(save_params=True)
    logger.info(">>> read dynamic params & build static network...")
    dynamic2static("dynamic_params", "static_params")
    build_static_network(load_pretrain="static_params")

    print("\n<========================>\n")

    logger.info(">>> build torch model & save params...")
    build_torch_model(save_params=True)
    logger.info(">>> read torch params & build dynamic network...")
    torch2dynamic("torch_model.params", "from_torch")
    build_dynamic_network(load_params="from_torch", use_structured_name=True)
