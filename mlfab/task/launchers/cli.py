"""Defines a launcher that can be toggled from the command line."""

import argparse
import sys
from typing import TYPE_CHECKING, Literal, get_args

from mlfab.task.base import RawConfigType
from mlfab.task.launchers.base import BaseLauncher
from mlfab.task.launchers.multi_process import MultiProcessLauncher
from mlfab.task.launchers.single_process import SingleProcessLauncher
from mlfab.task.launchers.slurm import SlurmLauncher

if TYPE_CHECKING:
    from mlfab.task.mixins.runnable import Config, RunnableMixin


LauncherChoice = Literal["single", "mp", "slurm"]


class CliLauncher(BaseLauncher):
    def launch(
        self,
        task: "type[RunnableMixin[Config]]",
        *cfgs: RawConfigType,
        use_cli: bool | list[str] = True,
    ) -> None:
        args = use_cli if isinstance(use_cli, list) else sys.argv[1:]
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument(
            "-l",
            "--launcher",
            choices=get_args(LauncherChoice),
            default="mp",
            help="The launcher to use; `single` for single-process, `mp` for multi-process, `slurm` for SLURM",
        )
        args, cli_args_rest = parser.parse_known_intermixed_args(args=args)
        launcher_choice: LauncherChoice = args.launcher
        use_cli_next: bool | list[str] = False if not use_cli else cli_args_rest

        match launcher_choice:
            case "single":
                SingleProcessLauncher().launch(task, *cfgs, use_cli=use_cli_next)
            case "mp":
                MultiProcessLauncher().launch(task, *cfgs, use_cli=use_cli_next)
            case "slurm":
                slurm_args, cli_args_rest = SlurmLauncher.parse_args_from_cli()
                SlurmLauncher(
                    partition=slurm_args.partition,
                    gpus_per_node=slurm_args.gpus_per_node,
                    num_nodes=slurm_args.num_nodes,
                    num_jobs=slurm_args.num_jobs,
                    account=slurm_args.account,
                ).launch(task, *cfgs, use_cli=use_cli_next)
            case _:
                raise ValueError(f"Invalid launcher choice: {launcher_choice}")
