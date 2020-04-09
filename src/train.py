from pytorch_lightning.callbacks import EarlyStopping
from optuna.integration.pytorch_lightning import _check_pytorch_lightning_availability
from pathlib import Path
import optuna
import pytorch_lightning as pl
from matplotlib import pyplot as plt
import torch
from .dict_logger import DictLogger
from .utils import PyTorchLightningPruningCallback
from .plot import plot_from_loader


def main(
    trial: optuna.Trial,
    PL_MODEL_CLS: pl.LightningModule,
    name: str,
    MODEL_DIR: Path = Path("./lightning_logs"),
    train=True,
    prune=True,
    PERCENT_TEST_EXAMPLES=0.5,
):
    # PyTorch Lightning will try to restore model parameters from previous trials if checkpoint
    # filenames match. Therefore, the filenames for each trial must be made unique.

    checkpoint_callback = pl.callbacks.ModelCheckpoint(
        MODEL_DIR / name / "version_{}".format(trial.number) / "chk",
        monitor="val_loss",
        mode="min",
    )

    # The default logger in PyTorch Lightning writes to event files to be consumed by
    # TensorBoard. We create a simple logger instead that holds the log in memory so that the
    # final accuracy can be obtained after optimization. When using the default logger, the
    # final accuracy could be stored in an attribute of the `Trainer` instead.
    logger = DictLogger(MODEL_DIR, name=name, version=trial.number)
    #     print("log_dir", logger.experiment.log_dir)
    hparams = dict(**trial.params, **trial.user_attrs)

    trainer = pl.Trainer(
        logger=logger,
        val_percent_check=PERCENT_TEST_EXAMPLES,
        checkpoint_callback=checkpoint_callback,
        max_epochs=hparams["max_nb_epochs"],
        gpus=-1 if torch.cuda.is_available() else None,
        early_stop_callback=PyTorchLightningPruningCallback(trial, monitor="val_loss")
        if prune
        else EarlyStopping(
            patience=hparams["patience"] * 2, monitor="val_loss", verbose=True
        ),
    )

    model = PL_MODEL_CLS(hparams)
    if train:
        trainer.fit(model)
    return model, trainer


def objective(trial, PL_MODEL_CLS):
    # see https://github.com/optuna/optuna/blob/cf6f02d/examples/pytorch_lightning_simple.py
    trial = PL_MODEL_CLS.add_suggest(trial)

    print("trial", trial.number, "params", trial.params)

    model, trainer = main(trial)

    # also report to tensorboard & print
    print("logger.metrics", model.logger.metrics[-1:])
    model.logger.experiment.add_hparams(trial.params, logger.metrics[-1])
    model.logger.save()

    return model.logger.metrics[-1]["val_loss"]


def add_number(trial: optuna.Trial, model_dir: Path):
    # For manual experiment we will start at -1 and deincr by 1
    versions = [int(s.stem.split("_")[-1]) for s in model_dir.glob("version_*")] + [-1]
    trial.number = min(versions) - 1
    print("trial.number", trial.number)
    return trial


def run_trial(
    name: str,
    PL_MODEL_CLS: pl.LightningModule,
    params: dict = {},
    user_attrs: dict = {},
    MODEL_DIR: Path = Path("./lightning_logs"),
    plot_from_loader=plot_from_loader,
):
    print(f"now run `tensorboard --logdir {MODEL_DIR}`")
    (MODEL_DIR / name).mkdir(parents=True, exist_ok=True)
    trial = optuna.trial.FixedTrial(params=params)
    trial = PL_MODEL_CLS.add_suggest(trial)
    trial = add_number(trial, MODEL_DIR / name)
    trial._user_attrs.update(user_attrs)
    model, trainer = main(
        trial, PL_MODEL_CLS, name=name, MODEL_DIR=MODEL_DIR, train=False, prune=False
    )
    try:
        trainer.fit(model)
    except KeyboardInterrupt:
        print('KeyboardInterrupt, skipping rest of training')
        pass

    # Plot
    loader = model.val_dataloader()
    dset_test = loader.dataset
    label_names = dset_test.label_names
    plot_from_loader(model.val_dataloader(), model, i=670, title='overfit val 670')
    plt.show()
    plot_from_loader(model.train_dataloader(), model, i=670, title='overfit train 670')
    plt.show()
    plot_from_loader(model.test_dataloader(), model, i=670, title='overfit test 670')
    plt.show()

    # Load checkpoint
    checkpoints = sorted(Path(trainer.checkpoint_callback.dirpath).glob("*.ckpt"))
    if len(checkpoints):
        checkpoint = checkpoints[-1]
        device = next(model.parameters()).device
        print(f"Loading checkpoint {checkpoint}")
        model = model.load_from_checkpoint(checkpoint).to(device)

        # Plot
        plot_from_loader(model.val_dataloader(), model, i=670, title='val 670')
        plt.show()
        plot_from_loader(model.train_dataloader(), model, i=670, title='train 670')
        plt.show()
        plot_from_loader(model.test_dataloader(), model, i=670, title='test 670')
        plt.show()

    try:
        trainer.test(model)
    except KeyboardInterrupt:
        print('KeyboardInterrupt, skipping rest of testing')
        pass
    return trial, trainer, model
