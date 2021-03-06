# Copyright 2020 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

__all__ = ['BatchNorm', 'BatchNorm0D', 'BatchNorm1D', 'BatchNorm2D',
           'Conv2D', 'ConvTranspose2D', 'Dropout', 'Linear',
           'MovingAverage', 'ExponentialMovingAverage', 'Sequential',
           'SyncedBatchNorm', 'SyncedBatchNorm0D', 'SyncedBatchNorm1D', 'SyncedBatchNorm2D']

import inspect
from typing import Callable, Iterable, Tuple, Optional, Union

from jax import numpy as jn, random as jr, lax

from objax import functional, random, util
from objax.constants import ConvPadding
from objax.module import ModuleList, Module
from objax.nn.init import kaiming_normal, xavier_normal
from objax.typing import JaxArray
from objax.variable import TrainVar, StateVar


class BatchNorm(Module):
    """Applies a batch normalization on different ranks of an input tensor.

    The module follows the operation described in Algorithm 1 of
    `Batch Normalization: Accelerating Deep Network Training by Reducing Internal Covariate Shift
    <https://arxiv.org/abs/1502.03167>`_.
    """

    def __init__(self, dims: Iterable[int], redux: Iterable[int], momentum: float = 0.999, eps: float = 1e-6):
        """Creates a BatchNorm module instance.

        Args:
            dims: shape of the batch normalization state variables.
            redux: list of indices of reduction axes. Batch norm statistics are computed by averaging over these axes.
            momentum: value used to compute exponential moving average of batch statistics.
            eps: small value which is used for numerical stability.
        """
        super().__init__()
        dims = tuple(dims)
        self.momentum = momentum
        self.eps = eps
        self.redux = tuple(redux)
        self.running_mean = StateVar(jn.zeros(dims))
        self.running_var = StateVar(jn.ones(dims))
        self.beta = TrainVar(jn.zeros(dims))
        self.gamma = TrainVar(jn.ones(dims))

    def __call__(self, x: JaxArray, training: bool) -> JaxArray:
        """Performs batch normalization of input tensor.

        Args:
            x: input tensor.
            training: if True compute batch normalization in training mode (accumulating batch statistics),
                otherwise compute in evaluation mode (using already accumulated batch statistics).

        Returns:
            Batch normalized tensor.
        """
        if training:
            m = x.mean(self.redux, keepdims=True)
            v = (x ** 2).mean(self.redux, keepdims=True) - m ** 2
            self.running_mean.value += (1 - self.momentum) * (m - self.running_mean.value)
            self.running_var.value += (1 - self.momentum) * (v - self.running_var.value)
        else:
            m, v = self.running_mean.value, self.running_var.value
        y = self.gamma.value * (x - m) * functional.rsqrt(v + self.eps) + self.beta.value
        return y


class BatchNorm0D(BatchNorm):
    """Applies a 0D batch normalization on a 2D-input batch of shape (N,C).

    The module follows the operation described in Algorithm 1 of
    `Batch Normalization: Accelerating Deep Network Training by Reducing Internal Covariate Shift
    <https://arxiv.org/abs/1502.03167>`_.
    """

    def __init__(self, nin: int, momentum: float = 0.999, eps: float = 1e-6):
        """Creates a BatchNorm0D module instance.

        Args:
            nin: number of channels in the input example.
            momentum: value used to compute exponential moving average of batch statistics.
            eps: small value which is used for numerical stability.
        """
        super().__init__((1, nin), (0,), momentum, eps)


class BatchNorm1D(BatchNorm):
    """Applies a 1D batch normalization on a 3D-input batch of shape (N,C,L).

    The module follows the operation described in Algorithm 1 of
    `Batch Normalization: Accelerating Deep Network Training by Reducing Internal Covariate Shift
    <https://arxiv.org/abs/1502.03167>`_.
    """

    def __init__(self, nin: int, momentum: float = 0.999, eps: float = 1e-6):
        """Creates a BatchNorm1D module instance.

        Args:
            nin: number of channels in the input example.
            momentum: value used to compute exponential moving average of batch statistics.
            eps: small value which is used for numerical stability.
        """
        super().__init__((1, nin, 1), (0, 2), momentum, eps)


class BatchNorm2D(BatchNorm):
    """Applies a 2D batch normalization on a 4D-input batch of shape (N,C,H,W).

    The module follows the operation described in Algorithm 1 of
    `Batch Normalization: Accelerating Deep Network Training by Reducing Internal Covariate Shift
    <https://arxiv.org/abs/1502.03167>`_.
    """

    def __init__(self, nin: int, momentum: float = 0.999, eps: float = 1e-6):
        """Creates a BatchNorm2D module instance.

        Args:
            nin: number of channels in the input example.
            momentum: value used to compute exponential moving average of batch statistics.
            eps: small value which is used for numerical stability.
        """
        super().__init__((1, nin, 1, 1), (0, 2, 3), momentum, eps)


class Conv2D(Module):
    """Applies a 2D convolution on a 4D-input batch of shape (N,C,H,W)."""

    def __init__(self,
                 nin: int,
                 nout: int,
                 k: Union[Tuple[int, int], int],
                 strides: Union[Tuple[int, int], int] = 1,
                 dilations: Union[Tuple[int, int], int] = 1,
                 groups: int = 1,
                 padding: ConvPadding = ConvPadding.SAME,
                 use_bias: bool = True,
                 w_init: Callable = kaiming_normal):
        """Creates a Conv2D module instance.

        Args:
            nin: number of channels of the input tensor.
            nout: number of channels of the output tensor.
            k: size of the convolution kernel, either tuple (height, width) or single number if they're the same.
            strides: convolution strides, either tuple (stride_y, stride_x) or single number if they're the same.
            dilations: spacing between kernel points (also known as astrous convolution),
                       either tuple (dilation_y, dilation_x) or single number if they're the same.
            groups: number of input and output channels group. When groups > 1 convolution operation is applied
                    individually for each group. nin and nout must both be divisible by groups.
            padding: padding of the input tensor, either Padding.SAME or Padding.VALID.
            use_bias: if True then convolution will have bias term.
            w_init: initializer for convolution kernel (a function that takes in a HWIO shape and returns a 4D matrix).
        """
        super().__init__()
        assert nin % groups == 0, 'nin should be divisible by groups'
        assert nout % groups == 0, 'nout should be divisible by groups'
        self.b = TrainVar(jn.zeros((nout, 1, 1))) if use_bias else None
        self.w = TrainVar(w_init((*util.to_tuple(k, 2), nin // groups, nout)))  # HWIO
        self.padding = padding
        self.strides = util.to_tuple(strides, 2)
        self.dilations = util.to_tuple(dilations, 2)
        self.groups = groups

    def __call__(self, x: JaxArray) -> JaxArray:
        """Returns the results of applying the convolution to input x."""
        y = lax.conv_general_dilated(x, self.w.value, self.strides, self.padding.value,
                                     rhs_dilation=self.dilations,
                                     feature_group_count=self.groups,
                                     dimension_numbers=('NCHW', 'HWIO', 'NCHW'))
        if self.b:
            y += self.b.value
        return y


class ConvTranspose2D(Conv2D):
    """Applies a 2D transposed convolution on a 4D-input batch of shape (N,C,H,W).

    This module can be seen as a transformation going in the opposite direction of a normal convolution, i.e.,
    from something that has the shape of the output of some convolution to something that has the shape of its input
    while maintaining a connectivity pattern that is compatible with said convolution.
    Note that ConvTranspose2D is consistent with
    `Conv2DTranspose <https://www.tensorflow.org/api_docs/python/tf/keras/layers/Conv2DTranspose>`_,
    of Tensorflow but is not consistent with
    `ConvTranspose2D <https://pytorch.org/docs/master/generated/torch.nn.ConvTranspose2d.html>`_
    of PyTorch due to kernel transpose and padding.
    """

    def __init__(self,
                 nin: int,
                 nout: int,
                 k: Union[Tuple[int, int], int],
                 strides: Union[Tuple[int, int], int] = 1,
                 dilations: Union[Tuple[int, int], int] = 1,
                 padding: ConvPadding = ConvPadding.SAME,
                 use_bias: bool = True,
                 w_init: Callable = kaiming_normal):
        """Creates a ConvTranspose2D module instance.

        Args:
            nin: number of channels of the input tensor.
            nout: number of channels of the output tensor.
            k: size of the convolution kernel, either tuple (height, width) or single number if they're the same.
            strides: convolution strides, either tuple (stride_y, stride_x) or single number if they're the same.
            dilations: spacing between kernel points (also known as astrous convolution),
                       either tuple (dilation_y, dilation_x) or single number if they're the same.
            padding: padding of the input tensor, either Padding.SAME or Padding.VALID.
            use_bias: if True then convolution will have bias term.
            w_init: initializer for convolution kernel (a function that takes in a HWIO shape and returns a 4D matrix).
        """
        super().__init__(nin=nout, nout=nin, k=k, strides=strides, padding=padding, use_bias=False, w_init=w_init)
        self.b = TrainVar(jn.zeros((nout, 1, 1))) if use_bias else None
        self.dilations = util.to_tuple(dilations, 2)

    def __call__(self, x: JaxArray) -> JaxArray:
        """Returns the results of applying the transposed convolution to input x."""
        y = lax.conv_transpose(x, self.w.value, self.strides, self.padding.value,
                               rhs_dilation=self.dilations,
                               dimension_numbers=('NCHW', 'HWIO', 'NCHW'), transpose_kernel=True)
        if self.b:
            y += self.b.value
        return y


class Dropout(Module):
    """In the training phase, a dropout layer zeroes some elements of the input tensor with probability 1-keep and
    scale the other elements by a factor of 1/keep."""

    def __init__(self, keep: float, generator=random.DEFAULT_GENERATOR):
        """Creates Dropout module instance.

        Args:
            keep: probability to keep element of the tensor.
            generator: optional argument with instance of ObJAX random generator.
        """
        self.keygen = generator
        self.keep = keep

    def __call__(self, x: JaxArray, training: bool, dropout_keep: Optional[float] = None) -> JaxArray:
        """Performs dropout of input tensor.

        Args:
            x: input tensor.
            training: if True then apply dropout to the input, otherwise keep input tensor unchanged.
            dropout_keep: optional argument, when set overrides dropout keep probability.

        Returns:
            Tensor with applied dropout.
        """
        keep = dropout_keep or self.keep
        if not training or keep >= 1:
            return x
        keep_mask = jr.bernoulli(self.keygen(), keep, x.shape)
        return jn.where(keep_mask, x / keep, 0)


class Linear(Module):
    """Applies a linear transformation on an input batch."""

    def __init__(self, nin: int, nout: int, use_bias: bool = True, w_init: Callable = xavier_normal):
        """Creates a Linear module instance.

        Args:
            nin: number of channels of the input tensor.
            nout: number of channels of the output tensor.
            use_bias: if True then linear layer will have bias term.
            w_init: weight initializer for linear layer (a function that takes in a IO shape and returns a 2D matrix).
        """
        super().__init__()
        self.b = TrainVar(jn.zeros(nout)) if use_bias else None
        self.w = TrainVar(w_init((nin, nout)))

    def __call__(self, x: JaxArray) -> JaxArray:
        """Returns the results of applying the linear transformation to input x."""
        y = jn.dot(x, self.w.value)
        if self.b:
            y += self.b.value
        return y


class MovingAverage(Module):
    """Computes moving average of an input batch."""

    def __init__(self, shape: Tuple[int, ...], buffer_size: int, init_value: float = 0):
        """Creates a MovingAverage module instance.

        Args:
            shape: shape of the input tensor.
            buffer_size: buffer size for moving average.
            init_value: initial value for moving average buffer.
        """
        self.buffer = StateVar(jn.zeros((buffer_size,) + shape) + init_value)

    def __call__(self, x: JaxArray) -> JaxArray:
        """Update the statistics using x and return the moving average."""
        self.buffer.value = jn.concatenate([self.buffer.value[1:], x[None]])
        return self.buffer.value.mean(0)


class ExponentialMovingAverage(Module):
    """computes exponential moving average (also called EMA or EWMA) of an input batch."""

    def __init__(self, shape: Tuple[int, ...], momentum: float = 0.999, init_value: float = 0):
        """Creates a ExponentialMovingAverage module instance.

        Args:
            shape: shape of the input tensor.
            momentum: momentum for exponential decrease of accumulated value.
            init_value: initial value for exponential moving average.
        """
        self.momentum = momentum
        self.avg = StateVar(jn.zeros(shape) + init_value)

    def __call__(self, x: JaxArray) -> JaxArray:
        """Update the statistics using x and return the exponential moving average."""
        self.avg.value += (self.avg.value - x) * (self.momentum - 1)
        return self.avg.value


class Sequential(ModuleList):
    """Executes modules in the order they were passed to the constructor."""

    def __call__(self, x: JaxArray, **kwargs) -> JaxArray:
        """Execute the sequence of operation contained on ``x`` and ``**kwargs`` and return result."""
        for f in self:
            s = inspect.signature(f).parameters
            if s:
                if next(reversed(s.values())).kind == inspect.Parameter.VAR_KEYWORD:
                    local_kwargs = kwargs
                else:
                    local_kwargs = {k: v for k, v in kwargs.items() if k in s}
            else:
                local_kwargs = {}
            x = f(x, **local_kwargs)
        return x


class SyncedBatchNorm(BatchNorm):
    """Synchronized batch normalization which aggregates batch statistics across all devices (GPUs/TPUs)."""

    def __call__(self, x: JaxArray, training: bool, batch_norm_update: bool = True) -> JaxArray:
        if training:
            m = functional.parallel.pmean(x.mean(self.redux, keepdims=True))
            v = functional.parallel.pmean((x ** 2).mean(self.redux, keepdims=True) - m ** 2)
            if batch_norm_update:
                self.running_mean.value += (1 - self.momentum) * (m - self.running_mean.value)
                self.running_var.value += (1 - self.momentum) * (v - self.running_var.value)
        else:
            m, v = self.running_mean.value, self.running_var.value
        y = self.gamma.value * (x - m) * functional.rsqrt(v + self.eps) + self.beta.value
        return y


class SyncedBatchNorm0D(SyncedBatchNorm):
    """Applies a 0D synchronized batch normalization on a 2D-input batch of shape (N,C).

    Synchronized batch normalization aggregated batch statistics across all devices (GPUs/TPUs) on each call.
    Compared to regular batch norm this usually leads to better accuracy at a slight performance cost.
    """

    def __init__(self, nin: int, momentum: float = 0.999, eps: float = 1e-6):
        """Creates a SyncedBatchNorm0D module instance.

        Args:
            nin: number of channels in the input example.
            momentum: value used to compute exponential moving average of batch statistics.
            eps: small value which is used for numerical stability.
        """
        super().__init__((1, nin), (0,), momentum, eps)


class SyncedBatchNorm1D(SyncedBatchNorm):
    """Applies a 1D synchronized batch normalization on a 3D-input batch of shape (N,C,L).

    Synchronized batch normalization aggregated batch statistics across all devices (GPUs/TPUs) on each call.
    Compared to regular batch norm this usually leads to better accuracy at a slight performance cost.
    """

    def __init__(self, nin: int, momentum: float = 0.999, eps: float = 1e-6):
        """Creates a SyncedBatchNorm1D module instance.

        Args:
            nin: number of channels in the input example.
            momentum: value used to compute exponential moving average of batch statistics.
            eps: small value which is used for numerical stability.
        """
        super().__init__((1, nin, 1), (0, 2), momentum, eps)


class SyncedBatchNorm2D(SyncedBatchNorm):
    """Applies a 2D synchronized batch normalization on a 4D-input batch of shape (N,C,H,W).

    Synchronized batch normalization aggregated batch statistics across all devices (GPUs/TPUs) on each call.
    Compared to regular batch norm this usually leads to better accuracy at a slight performance cost.
    """

    def __init__(self, nin: int, momentum: float = 0.999, eps: float = 1e-6):
        """Creates a SyncedBatchNorm2D module instance.

        Args:
            nin: number of channels in the input example.
            momentum: value used to compute exponential moving average of batch statistics.
            eps: small value which is used for numerical stability.
        """
        super().__init__((1, nin, 1, 1), (0, 2, 3), momentum, eps)
