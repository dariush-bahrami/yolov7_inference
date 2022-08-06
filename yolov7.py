from dataclasses import dataclass
from typing import NamedTuple, Sequence

import numpy as np
import torch
from numpy import random

from models.experimental import attempt_load
from utils.datasets import letterbox
from utils.general import check_img_size, non_max_suppression, scale_coords, set_logging
from utils.plots import plot_one_box
from utils.torch_utils import TracedModel, select_device


class BoundingBox(NamedTuple):
    xmin: int
    ymin: int
    xmax: int
    ymax: int


class Color(NamedTuple):
    red: int
    green: int
    blue: int


class Label(NamedTuple):
    index: int
    name: str
    color: Color


class DetectedObject(NamedTuple):
    label: Label
    confidence: float
    bounding_box: BoundingBox


class YOLOv7Prediction:
    def __init__(self, image: np.ndarray, detected_objects: Sequence[DetectedObject]):
        self.__image = image
        self.detected_objects = detected_objects
        self.__visualized = False

    def visualize(self):
        if not self.__visualized:
            self.__plot_boxes()
            self.__visualized = True
            return self.__image
        else:
            return self.__image

    def __plot_boxes(self):
        for detected_object in self.detected_objects:
            box_label = "{} {:.2f}".format(
                detected_object.label.name, detected_object.confidence
            )
            plot_one_box(
                detected_object.bounding_box,
                self.__image,
                label=box_label,
                color=detected_object.label.color,
                line_thickness=3,
            )

    def __str__(self):
        return "YOLOv7 Detection Result with {} detected objects: {}".format(
            len(self.detected_objects),
            [i.label.name for i in self.detected_objects],
        )

    def __repr__(self):
        return self.__str__()


@dataclass
class InferenceArgs:
    weights: str
    image_size: int = 640
    confidence_threshold: float = 0.25
    iou_threshold: float = 0.45
    device: str = ""
    agnostic_nms: bool = False
    trace: bool = True


class YOLOv7:
    def __init__(self, inference_args: InferenceArgs):
        self.inference_args = inference_args

        #################

        # Initialize
        set_logging()
        self.device = select_device(self.inference_args.device)
        self.half = self.device.type != "cpu"  # half precision only supported on CUDA

        # Load model
        # load FP32 model
        self.model = attempt_load(
            self.inference_args.weights,
            map_location=self.device,
        )
        self.stride = int(self.model.stride.max())  # model stride
        # check img_size
        self.inference_args.image_size = check_img_size(
            self.inference_args.image_size,
            s=self.stride,
        )

        if self.inference_args.trace:
            self.model = TracedModel(
                self.model, self.device, self.inference_args.image_size
            )

        if self.half:
            self.model.half()  # to FP16

        # Get names and colors
        label_names = (
            self.model.module.names
            if hasattr(self.model, "module")
            else self.model.names
        )
        colors = [[random.randint(0, 255) for _ in range(3)] for _ in label_names]
        labels = []
        for i, (name, color) in enumerate(zip(label_names, colors)):
            labels.append(Label(i, name, Color(*color)))
        self.labels = labels

    def transform(self, image: np.ndarray) -> torch.Tensor:
        image = letterbox(image, self.inference_args.image_size, stride=self.stride)[0]
        image = image.transpose(2, 0, 1)  # HWC to CHW
        image = np.ascontiguousarray(image)
        image = torch.from_numpy(image).to(self.device)
        image = image.half() if self.half else image.float()  # uint8 to fp16/32
        image /= 255.0  # 0 - 255 to 0.0 - 1.0
        image = image.unsqueeze(0)  # add batch dimension
        return image

    def post_process(
        self,
        original_image: np.ndarray,
        transformed_image: torch.Tensor,
        outputs: torch.Tensor,
    ) -> YOLOv7Prediction:
        # Sclae bounding boxes back to original image shape
        outputs[:, :4] = scale_coords(
            transformed_image.shape[2:], outputs[:, :4], original_image.shape
        ).round()
        bboxes = outputs[:, :4].cpu().numpy()
        scores = outputs[:, 4].cpu().numpy()
        label_indexes = outputs[:, 5].cpu().numpy()
        detected_objects = []
        for label_index, score, bbox in zip(label_indexes, scores, bboxes):
            bbox = BoundingBox(*map(int, bbox))
            detected_objects.append(
                DetectedObject(
                    label=self.labels[int(label_index)],
                    confidence=score.item(),
                    bounding_box=bbox,
                )
            )
        return YOLOv7Prediction(original_image, detected_objects)

    @torch.no_grad()
    def __call__(self, image: np.ndarray):
        transformed_image = self.transform(image)
        prediction = self.model(transformed_image, augment=False)[0]
        prediction = non_max_suppression(
            prediction,
            self.inference_args.confidence_threshold,
            self.inference_args.iou_threshold,
            classes=None,
            agnostic=self.inference_args.agnostic_nms,
        )
        # remove batch dimension
        prediction = prediction[0]
        processed_prediction = self.post_process(image, transformed_image, prediction)
        return processed_prediction

    def __str__(self):
        return "YOLOv7 Inference Model"

    def __repr__(self):
        return self.__str__()
