# Owner(s): ["oncall: distributed"]

import sys

import torch
from torch import distributed as dist
from torch.distributed._fsdp import FullyShardedDataParallel as FSDP
from torch.nn import Linear, Module
from torch.nn.parallel import DistributedDataParallel
from torch.optim import SGD
from torch.testing._internal.common_distributed import skip_if_lt_x_gpu
from torch.testing._internal.common_fsdp import (
    FSDPTest,
    get_full_params,
)
from torch.testing._internal.common_utils import TEST_WITH_DEV_DBG_ASAN, run_tests


if not dist.is_available():
    print("Distributed not available, skipping tests", file=sys.stderr)
    sys.exit(0)

if TEST_WITH_DEV_DBG_ASAN:
    print(
        "Skip dev-asan as torch + multiprocessing spawn have known issues",
        file=sys.stderr,
    )
    sys.exit(0)


class Model(Module):
    def __init__(self, wrap_fsdp):
        super().__init__()
        # keep everything deterministic for model initialization
        torch.manual_seed(0)
        self.inner = Linear(4, 4)
        if wrap_fsdp:
            self.inner = FSDP(self.inner)
        self.outer = Linear(4, 5)

    def forward(self, x):
        y = self.inner(x)
        return self.outer(y)


# Test pure fp16 training, also testing the case when the parameter's data type is
# changed after FSDP wrapping and before training loop starts.
# Only run one step for comparision, as usually grad scaler is needed to avoid NaN value
# after first step.
class TestPureFP16(FSDPTest):
    def _dist_train(self, wrap_fsdp):
        # keep everything deterministic for input data
        torch.manual_seed(0)

        model = Model(wrap_fsdp).cuda()
        if wrap_fsdp:
            model = FSDP(model)
        else:
            model = DistributedDataParallel(model, device_ids=[self.rank])
        model.half()
        optim = SGD(model.parameters(), lr=0.1)

        in_data = torch.rand(64, 4).cuda().half()
        in_data.requires_grad = True
        for _ in range(1):
            out = model(in_data)
            out.sum().backward()
            optim.step()
            optim.zero_grad()

        if wrap_fsdp:
            get_full_params(model)

        return list(model.parameters())

    @skip_if_lt_x_gpu(2)
    def test_pure_fp16(self):
        # DDP
        ddp_state = self._dist_train(wrap_fsdp=False)

        # FSDP
        fsdp_state = self._dist_train(wrap_fsdp=True)

        self.assertEqual(ddp_state, fsdp_state)


if __name__ == "__main__":
    run_tests()