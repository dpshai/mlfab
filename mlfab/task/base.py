"""Defines the base task interface.

This interface is built upon by a large number of other interfaces which
compose various functionality into a single cohesive unit. The base task
just stores the configuration and provides hooks which are overridden by
upstream classes.
"""

import functools
import inspect
import logging
import signal
import sys
from dataclasses import dataclass, is_dataclass
from pathlib import Path
from types import FrameType, TracebackType
from typing import Generic, Self, TypeVar, cast

from omegaconf import Container, DictConfig, OmegaConf
from torch import Tensor, nn

from mlfab.core.state import State
from mlfab.utils.text import camelcase_to_snakecase

logger = logging.getLogger(__name__)


@dataclass
class BaseConfig:
    pass


Config = TypeVar("Config", bound=BaseConfig)

RawConfigType = BaseConfig | dict | DictConfig | str | Path


def _load_as_dict(path: str | Path) -> DictConfig:
    cfg = OmegaConf.load(path)
    if not isinstance(cfg, DictConfig):
        raise TypeError(f"Config file at {path} must be a dictionary, not {type(cfg)}!")
    return cfg


def get_config(cfg: RawConfigType, task_path: Path) -> DictConfig:
    if isinstance(cfg, (str, Path)):
        cfg = Path(cfg)
        if cfg.exists():
            cfg = _load_as_dict(cfg)
        elif task_path is not None and len(cfg.parts) == 1 and (other_cfg_path := task_path.parent / cfg).exists():
            cfg = _load_as_dict(other_cfg_path)
        else:
            raise FileNotFoundError(f"Could not find config file at {cfg}!")
    elif isinstance(cfg, dict):
        cfg = OmegaConf.create(cfg)
    elif is_dataclass(cfg):
        cfg = OmegaConf.structured(cfg)
    return cast(DictConfig, cfg)


class BaseTask(nn.Module, Generic[Config]):
    def __init__(self, config: Config) -> None:
        super().__init__()

        self.config = config

        if isinstance(self.config, Container):
            OmegaConf.resolve(self.config)

    def on_before_forward_step(self, state: State) -> None:
        pass

    def on_after_forward_step(self, state: State) -> None:
        pass

    def on_after_compute_loss(self, state: State) -> None:
        pass

    def on_step_start(self, state: State) -> None:
        pass

    def on_step_end(self, state: State, loss_dict: dict[str, Tensor]) -> None:
        pass

    def on_epoch_start(self, state: State) -> None:
        pass

    def on_epoch_end(self, state: State) -> None:
        pass

    def on_training_start(self, state: State) -> None:
        pass

    def on_training_end(self, state: State) -> None:
        pass

    def on_before_save_checkpoint(self, ckpt_path: Path) -> None:
        pass

    def on_after_save_checkpoint(self, ckpt_path: Path) -> None:
        pass

    def on_exit(self, sig: signal.Signals, frame: FrameType | None, state: State) -> None:
        pass

    def load_task_state_dict(self, state_dict: dict, strict: bool = True, assign: bool = False) -> None:
        weights = state_dict.pop("weights")
        return self.load_state_dict(weights, strict=strict, assign=assign)

    def task_state_dict(self) -> dict:
        return {"weights": self.state_dict()}

    @functools.cached_property
    def task_class_name(self) -> str:
        return self.__class__.__name__

    @functools.cached_property
    def task_name(self) -> str:
        return camelcase_to_snakecase(self.task_class_name)

    @functools.cached_property
    def task_path(self) -> Path:
        return Path(inspect.getfile(self.__class__))

    @functools.cached_property
    def task_module(self) -> str:
        if (mod := inspect.getmodule(self.__class__)) is None:
            raise RuntimeError(f"Could not find module for task {self.__class__}!")
        if (spec := mod.__spec__) is None:
            raise RuntimeError(f"Could not find spec for module {mod}!")
        return spec.name

    @property
    def task_key(self) -> str:
        return f"{self.task_module}.{self.task_class_name}"

    @classmethod
    def from_task_key(cls, task_key: str) -> type[Self]:
        task_module, task_class_name = task_key.rsplit(".", 1)
        try:
            mod = __import__(task_module)
        except ImportError as e:
            raise ImportError(f"Could not import module {task_module} for task {task_key}") from e
        if not hasattr(mod, task_class_name):
            raise RuntimeError(f"Could not find class {task_class_name} in module {task_module}")
        task_class = getattr(mod, task_class_name)
        if not issubclass(task_class, cls):
            raise RuntimeError(f"Class {task_class_name} in module {task_module} is not a subclass of {cls}")
        return task_class

    def debug(self) -> bool:
        return False

    @property
    def debugging(self) -> bool:
        return self.debug()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, _t: type[BaseException] | None, _e: BaseException | None, _tr: TracebackType | None) -> None:
        pass

    @classmethod
    def get_config_class(cls) -> type[Config]:
        """Recursively retrieves the config class from the generic type.

        Returns:
            The parsed config class.

        Raises:
            ValueError: If the config class cannot be found, usually meaning
            that the generic class has not been used correctly.
        """
        if hasattr(cls, "__orig_bases__"):
            for base in cls.__orig_bases__:
                if hasattr(base, "__args__"):
                    for arg in base.__args__:
                        if issubclass(arg, BaseConfig):
                            return arg

        raise ValueError(
            "The config class could not be parsed from the generic type, which usually means that the task is not "
            "being instantiated correctly. Your class should be defined as follows:\n\n"
            "  class ExampleTask(mlfab.Task[Config]):\n      ...\n\nThis lets the both the task and the type "
            "checker know what config the task is using."
        )

    @classmethod
    def get_config(cls, *cfgs: RawConfigType, use_cli: bool = True) -> Config:
        """Builds the structured config from the provided config classes.

        Args:
            cfgs: The config classes to merge. If a string or Path is provided,
                it will be loaded as a YAML file.
            use_cli: Whether to allow additional overrides from the CLI.

        Returns:
            The merged configs.
        """
        task_path = Path(inspect.getfile(cls))
        cfg = OmegaConf.structured(cls.get_config_class())
        cfg = OmegaConf.merge(cfg, *(get_config(other_cfg, task_path) for other_cfg in cfgs))
        if use_cli:
            if "-h" in sys.argv or "--help" in sys.argv:
                sys.stderr.write(OmegaConf.to_yaml(cfg))
                sys.stderr.flush()
                sys.exit(0)
            cfg = OmegaConf.merge(cfg, OmegaConf.from_cli())
        return cast(Config, cfg)

    @classmethod
    def config_str(cls, *cfgs: RawConfigType, use_cli: bool = True) -> str:
        return OmegaConf.to_yaml(cls.get_config(*cfgs, use_cli=use_cli))

    @classmethod
    def get_task(cls, *cfgs: RawConfigType, use_cli: bool = True) -> Self:
        """Builds the task from the provided config classes.

        Args:
            cfgs: The config classes to merge. If a string or Path is provided,
                it will be loaded as a YAML file.
            use_cli: Whether to allow additional overrides from the CLI.

        Returns:
            The task.
        """
        cfg = cls.get_config(*cfgs, use_cli=use_cli)
        return cls(cfg)
