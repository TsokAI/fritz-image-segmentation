"""Train an ICNet Model on ADE20K Data."""

import argparse
import keras
import logging
import sys
import os

from tensorflow.python.lib.io import file_io

from image_segmentation.icnet import ICNetModelFactory
from image_segmentation.data_generator import ADE20KDatasetBuilder

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('train')


def train(argv):
    """Train an ICNet model."""
    parser = argparse.ArgumentParser(
        description='Train an ICNet model.'
    )
    # Data options
    parser.add_argument(
        '-d', '--tfrecord-data', type=str, required=True,
        help='A TFRecord file containing images and segmentation masks.'
    )
    parser.add_argument(
        '-l', '--label-filename', type=str, required=True,
        help='A file containing a single label per line.'
    )
    parser.add_argument(
        '-s', '--image-size', type=int, default=768,
        help=('The pixel dimension of model input and output. Images '
              'will be square.')
    )
    parser.add_argument(
        '-a', '--alpha', type=float, default=1.0,
        help='The width multiplier for the network'
    )
    parser.add_argument(
        '--augment-images', type=bool, default=True,
        help='turn on image augmentation.'
    )
    parser.add_argument(
        '--list-labels', action='store_true',
        help='If true, print a full list of object labels.'
    )
    # Training options
    parser.add_argument(
        '-b', '--batch-size', type=int, default=8,
        help='The training batch_size.'
    )
    parser.add_argument(
        '--lr', type=float, default=0.001, help='The learning rate.'
    )
    parser.add_argument(
        '-n', '--num-steps', type=int, default=1000,
        help='Number of training steps to perform'
    )
    parser.add_argument(
        '--steps-per-epoch', type=int, default=100,
        help='Number of training steps to perform between model checkpoints'
    )
    parser.add_argument(
        '-o', '--output', type=str, required=True,
        help='An output file to save the trained model.')
    parser.add_argument(
        '--fine-tune-checkpoint', type=str,
        help='A Keras model checkpoint to load and continue training.'
    )
    parser.add_argument(
        '--gcs-bucket', type=str,
        help='A GCS Bucket to save models too.'
    )

    args, unknown = parser.parse_known_args()

    class_labels = ADE20KDatasetBuilder.load_class_labels(
        args.label_filename)
    if args.list_labels:
        logger.info('Labels:')
        labels = ''
        for label in class_labels:
            labels += '%s\n' % label
        logger.info(labels)
        sys.exit()

    n_classes = len(class_labels)

    dataset = ADE20KDatasetBuilder.build(
        args.tfrecord_data,
        n_classes=n_classes,
        batch_size=args.batch_size,
        image_size=(args.image_size, args.image_size),
        augment_images=args.augment_images
    )

    iterator = dataset.make_one_shot_iterator()
    example = iterator.get_next()

    icnet = ICNetModelFactory.build(
        args.image_size,
        n_classes,
        weights_path=args.fine_tune_checkpoint,
        train=True,
        input_tensor=example['image'],
        alpha=args.alpha
    )

    optimizer = keras.optimizers.Adam(lr=args.lr)
    icnet.compile(
        optimizer,
        loss=keras.losses.categorical_crossentropy,
        loss_weights=[1.0, 0.4, 0.16],
        metrics=['categorical_accuracy'],
        target_tensors=[
            example['mask_4'], example['mask_8'], example['mask_16']
        ]
    )

    callbacks = [
        keras.callbacks.ModelCheckpoint(
            args.output,
            verbose=0,
            mode='auto',
            period=1
        ),
    ]

    if args.gcs_bucket:
        callbacks.append(SaveCheckpointToGCS(args.output, args.gcs_bucket))

    icnet.fit(
        steps_per_epoch=args.steps_per_epoch,
        epochs=int(args.num_steps / args.steps_per_epoch) + 1,
        callbacks=callbacks,
    )


class SaveCheckpointToGCS(keras.callbacks.Callback):
    """A callback to save local model checkpoints to GCS."""

    def __init__(self, local_filename, gcs_filename):
        """Save a checkpoint to GCS.

        Args:
            local_filename (str): the path of the local checkpoint
            gcs_filename (str): the GCS bucket to save the model to
        """
        self.gcs_filename = gcs_filename
        self.local_filename = local_filename

    @staticmethod
    def _copy_file_to_gcs(job_dir, file_path):
        gcs_url = os.path.join(job_dir, file_path)
        logger.info('Saving models to GCS: %s' % gcs_url)
        with file_io.FileIO(file_path, mode='rb') as input_f:
            with file_io.FileIO(gcs_url, mode='w+') as output_f:
                output_f.write(input_f.read())

    def on_epoch_end(self, epoch, logs={}):
        """Save model to GCS on epoch end.

        Args:
            epoch (int): the epoch number
            logs (dict, optional): logs dict
        """
        basename = os.path.basename(self.local_filename)
        self._copy_file_to_gcs(self.gcs_filename, basename)


if __name__ == '__main__':
    train(sys.argv[1:])
