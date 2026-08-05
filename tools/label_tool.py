#!/usr/bin/env python3
"""
ONNX-Assisted Labeling Tool
----------------------------
Auto-annotates images with a YOLOv5/YOLOv7-style ONNX model (anchor-based,
exported with --grid so the output is already decoded: [cx,cy,w,h,obj,cls...]
in pixel coordinates of the model's input size). Works with ANY image
resolution: each image is letterboxed to the model's input size for
inference, and predicted boxes are mapped back to the image's original
pixel coordinates, so labels are always saved relative to the source image.

You review the auto-generated boxes, fix/move/resize/delete/reclass them,
and save. Labels are stored as standard YOLO .txt files next to the images,
so the same folder can be fed back into training to keep improving the model.

Install:
    pip install PySide6 onnxruntime opencv-python numpy

Run:
    python labeling_tool.py
"""
import sys
import os
import glob
import numpy as np
import cv2
import onnxruntime as ort

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QListWidget,
    QListWidgetItem, QPushButton, QLabel, QFileDialog, QGraphicsView, QGraphicsScene,
    QGraphicsRectItem, QGraphicsPixmapItem, QSlider, QComboBox, QMessageBox,
    QStatusBar, QInputDialog, QDoubleSpinBox, QFormLayout, QFrame,
    QLineEdit, QScrollArea
)
from PySide6.QtGui import QPixmap, QImage, QPen, QColor, QBrush, QKeySequence, QShortcut, QFont
from PySide6.QtCore import Qt, QRectF, QPointF

COLORS = ["#00e5ff", "#ff3d71", "#a3ff73", "#ffb703", "#c77dff", "#ff6b6b",
          "#4cc9f0", "#f4a261", "#90e0ef", "#e63946", "#2a9d8f", "#e9c46a"]

IMG_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp")

# ---------------------------------------------------------------------------
# Dark "label-studio" theme (mirrors the companion HTML tool's look & feel:
# JetBrains Mono, cyan/pink/green accents on a near-black surface stack).
# ---------------------------------------------------------------------------
BG = "#0a0b0d"
SURFACE = "#111318"
SURFACE2 = "#181c24"
BORDER = "#232830"
ACCENT = "#00e5ff"
ACCENT2 = "#ff3d71"
ACCENT3 = "#a3ff73"
TEXT = "#e8edf5"
MUTED = "#5a6478"

DARK_QSS = f"""
QMainWindow, QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-family: 'JetBrains Mono', 'Consolas', monospace;
    font-size: 12px;
}}
QMainWindow::separator {{ background-color: {BORDER}; width: 1px; height: 1px; }}
QLabel {{ background: transparent; }}
QLabel[role="title"] {{
    font-weight: 700; font-size: 13px; color: {ACCENT}; letter-spacing: 1px;
}}
QLabel[role="section"] {{
    font-weight: 700; font-size: 10px; color: {MUTED}; letter-spacing: 1.5px;
    text-transform: uppercase; padding: 6px 2px 4px 2px;
}}
QLabel[role="hint"] {{ color: {MUTED}; font-size: 10px; }}
QWidget#header, QWidget#sidebar, QWidget#rightPanel, QWidget#toolbar,
QWidget#navBar, QWidget#statsBar {{
    background-color: {SURFACE};
    border-color: {BORDER};
}}
QWidget#header {{ border-bottom: 1px solid {BORDER}; }}
QWidget#sidebar {{ border-right: 1px solid {BORDER}; }}
QWidget#rightPanel {{ border-left: 1px solid {BORDER}; }}
QWidget#toolbar {{ border-bottom: 1px solid {BORDER}; }}
QWidget#navBar {{ border-bottom: 1px solid {BORDER}; }}
QWidget#statsBar {{ border-top: 1px solid {BORDER}; }}
QGraphicsView {{ background-color: {BG}; border: none; }}
QPushButton {{
    background-color: {SURFACE2};
    border: 1px solid {BORDER};
    color: {TEXT};
    padding: 6px 12px;
    border-radius: 4px;
}}
QPushButton:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton:checked {{ border-color: {ACCENT}; color: {ACCENT}; background-color: {SURFACE2}; }}
QPushButton:disabled {{ color: {MUTED}; border-color: {BORDER}; }}
QPushButton[variant="primary"] {{
    background-color: {ACCENT}; color: #000; font-weight: 700; border-color: {ACCENT};
}}
QPushButton[variant="primary"]:hover {{ background-color: #00c2d9; border-color: #00c2d9; color: #000; }}
QPushButton[variant="danger"] {{ border-color: {ACCENT2}; color: {ACCENT2}; }}
QPushButton[variant="danger"]:hover {{ background-color: {ACCENT2}; color: #fff; }}
QPushButton[variant="flat"] {{
    background: transparent; border: none; color: {MUTED}; padding: 2px 6px; font-size: 13px;
}}
QPushButton[variant="flat"]:hover {{ color: {ACCENT2}; border: none; background: transparent; }}
QListWidget {{
    background-color: {BG};
    border: 1px solid {BORDER};
    border-radius: 4px;
    outline: none;
}}
QListWidget::item {{ padding: 6px; border-radius: 4px; }}
QListWidget::item:selected {{ background-color: {SURFACE2}; color: {ACCENT}; }}
QListWidget::item:hover {{ background-color: {SURFACE2}; }}
QComboBox, QDoubleSpinBox, QLineEdit {{
    background-color: {BG};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 6px;
    color: {TEXT};
}}
QComboBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {{ border-color: {ACCENT}; }}
QStatusBar {{ background-color: {SURFACE}; color: {MUTED}; border-top: 1px solid {BORDER}; }}
QSlider::groove:horizontal {{ background: {BORDER}; height: 4px; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {ACCENT}; width: 13px; height: 13px; margin: -5px 0; border-radius: 6px;
}}
QSplitter::handle {{ background-color: {BORDER}; }}
QScrollBar:vertical {{ background: transparent; width: 8px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 20px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
"""


class ClassRow(QFrame):
    """One row in the class palette: color dot, name, number-key badge, delete."""

    def __init__(self, index, name, color, key_num, is_active, on_click, on_delete):
        super().__init__()
        self.index = index
        self._on_click = on_click
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            f"QFrame {{ border: 1px solid {ACCENT if is_active else 'transparent'}; "
            f"background: {SURFACE2 if is_active else 'transparent'}; border-radius: 4px; }}"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 5, 6, 5)
        lay.setSpacing(8)
        dot = QLabel()
        dot.setFixedSize(10, 10)
        dot.setStyleSheet(f"background:{color}; border-radius:2px;")
        lay.addWidget(dot)
        lbl = QLabel(name)
        lbl.setStyleSheet(f"color:{color};")
        lay.addWidget(lbl, 1)
        if key_num is not None:
            key = QLabel(str(key_num))
            key.setStyleSheet(
                f"color:{MUTED}; background:{BG}; border:1px solid {BORDER}; "
                f"border-radius:3px; padding:1px 5px; font-size:10px;"
            )
            lay.addWidget(key)
        delbtn = QPushButton("✕")
        delbtn.setProperty("variant", "flat")
        delbtn.setFixedSize(20, 20)
        delbtn.clicked.connect(lambda: on_delete(index))
        lay.addWidget(delbtn)

    def mousePressEvent(self, event):
        self._on_click(self.index)
        super().mousePressEvent(event)


class AnnotationRow(QFrame):
    """One row in the annotations panel: color dot, class name + coords, delete."""

    def __init__(self, box, class_names, is_selected, on_click, on_delete):
        super().__init__()
        self.box = box
        self._on_click = on_click
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            f"QFrame {{ border: 1px solid {ACCENT if is_selected else BORDER}; "
            f"background: {SURFACE2 if is_selected else 'transparent'}; border-radius: 4px; }}"
        )
        color = box.color().name()
        name = class_names[box.class_id] if 0 <= box.class_id < len(class_names) else f"cls{box.class_id}"
        r = box.rect()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 5, 6, 5)
        lay.setSpacing(8)
        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background:{color}; border-radius:2px;")
        lay.addWidget(dot)
        text = QVBoxLayout()
        text.setSpacing(0)
        lbl = QLabel(name)
        lbl.setStyleSheet(f"color:{TEXT}; font-size:11px;")
        coords = QLabel(f"{int(r.x())},{int(r.y())} {int(r.width())}×{int(r.height())}")
        coords.setStyleSheet(f"color:{MUTED}; font-size:9px;")
        text.addWidget(lbl)
        text.addWidget(coords)
        lay.addLayout(text, 1)
        delbtn = QPushButton("✕")
        delbtn.setProperty("variant", "flat")
        delbtn.setFixedSize(20, 20)
        delbtn.clicked.connect(lambda: on_delete(box))
        lay.addWidget(delbtn)

    def mousePressEvent(self, event):
        self._on_click(self.box)
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# ONNX inference (YOLOv5 / YOLOv7 --grid export: output already sigmoid'd,
# xywh in pixel space of the model input, layout [cx,cy,w,h,obj,cls0,cls1,...])
# ---------------------------------------------------------------------------
class OnnxDetector:
    def __init__(self, model_path):
        self.session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        shape = self.session.get_inputs()[0].shape
        h = shape[2] if isinstance(shape[2], int) else 640
        w = shape[3] if isinstance(shape[3], int) else 640
        self.input_size = (h, w)  # (H, W)

    @staticmethod
    def letterbox(img, new_shape, color=(114, 114, 114)):
        h, w = img.shape[:2]
        nh, nw = new_shape
        r = min(nh / h, nw / w)
        uw, uh = int(round(w * r)), int(round(h * r))
        resized = cv2.resize(img, (uw, uh), interpolation=cv2.INTER_LINEAR)
        dw, dh = (nw - uw) / 2, (nh - uh) / 2
        top, bottom = int(round(dh - 0.1)), int(round(dh + 0.1))
        left, right = int(round(dw - 0.1)), int(round(dw + 0.1))
        padded = cv2.copyMakeBorder(resized, top, bottom, left, right,
                                     cv2.BORDER_CONSTANT, value=color)
        return padded, r, left, top

    def infer(self, bgr_img, conf_thres=0.25, iou_thres=0.45, num_classes=None):
        h0, w0 = bgr_img.shape[:2]
        img, r, padx, pady = self.letterbox(bgr_img, self.input_size)
        blob = img[:, :, ::-1].transpose(2, 0, 1).astype(np.float32) / 255.0
        blob = np.expand_dims(blob, 0)
        out = self.session.run(None, {self.input_name: blob})[0]
        out = np.squeeze(out)
        if out.ndim == 1:
            out = out[None, :]
        if out.shape[0] == 0:
            return []

        # Exported models come in two very different shapes:
        #  - raw grid (YOLOv5/v7 --grid): thousands of anchor rows,
        #    [cx, cy, w, h, obj, cls0, cls1, ...] in letterboxed pixel space,
        #    needs manual threshold + NMS.
        #  - end2end (YOLOv7 --end2end, NMS baked into the graph): a handful
        #    of already-NMS'd rows, [batch_id, x1, y1, x2, y2, cls_id, score]
        #    (7 cols) or [x1, y1, x2, y2, score, cls_id] (6 cols).
        # Row count is the reliable signal: anchor-grid outputs are always in
        # the thousands; end2end outputs are just the final detections.
        if out.ndim == 2 and out.shape[1] in (6, 7) and out.shape[0] < 1000:
            return self._decode_end2end(out, r, padx, pady, w0, h0, conf_thres)
        return self._decode_raw_grid(out, r, padx, pady, w0, h0, conf_thres, iou_thres, num_classes)

    def _decode_end2end(self, out, r, padx, pady, w0, h0, conf_thres):
        if out.shape[1] == 7:
            x1, y1, x2, y2 = out[:, 1], out[:, 2], out[:, 3], out[:, 4]
            cls_ids, scores = out[:, 5], out[:, 6]
        else:  # 6 columns, no batch id
            x1, y1, x2, y2 = out[:, 0], out[:, 1], out[:, 2], out[:, 3]
            scores, cls_ids = out[:, 4], out[:, 5]

        results = []
        for i in range(len(out)):
            if scores[i] < conf_thres:
                continue
            bx1 = (x1[i] - padx) / r
            by1 = (y1[i] - pady) / r
            bx2 = (x2[i] - padx) / r
            by2 = (y2[i] - pady) / r
            bx1, bx2 = np.clip([bx1, bx2], 0, w0 - 1)
            by1, by2 = np.clip([by1, by2], 0, h0 - 1)
            if bx2 - bx1 < 2 or by2 - by1 < 2:
                continue
            results.append((float(bx1), float(by1), float(bx2), float(by2),
                             int(round(cls_ids[i])), float(scores[i])))
        return results

    def _decode_raw_grid(self, out, r, padx, pady, w0, h0, conf_thres, iou_thres, num_classes):
        nc = num_classes if num_classes else out.shape[1] - 5
        obj = out[:, 4]
        mask = obj > conf_thres
        out = out[mask]
        if len(out) == 0:
            return []

        cls_scores = out[:, 5:5 + nc]
        cls_ids = np.argmax(cls_scores, axis=1)
        cls_conf = cls_scores[np.arange(len(out)), cls_ids]
        scores = out[:, 4] * cls_conf
        mask2 = scores > conf_thres
        out, cls_ids, scores = out[mask2], cls_ids[mask2], scores[mask2]
        if len(out) == 0:
            return []

        cx, cy, w, h = out[:, 0], out[:, 1], out[:, 2], out[:, 3]
        x1, y1 = cx - w / 2, cy - h / 2

        # per-class NMS via class offset trick; cv2.dnn.NMSBoxes wants (x, y, w, h)
        max_wh = max(self.input_size)
        offset = cls_ids.astype(np.float32) * max_wh
        boxes_for_nms = np.stack([x1 + offset, y1 + offset, w, h], axis=1)
        idxs = cv2.dnn.NMSBoxes(
            boxes_for_nms.tolist(), scores.tolist(), conf_thres, iou_thres
        )
        idxs = np.array(idxs).flatten() if len(idxs) else np.array([], dtype=int)

        results = []
        for i in idxs:
            bx1 = (x1[i] - padx) / r
            by1 = (y1[i] - pady) / r
            bx2 = (x1[i] + w[i] - padx) / r
            by2 = (y1[i] + h[i] - pady) / r
            bx1, bx2 = np.clip([bx1, bx2], 0, w0 - 1)
            by1, by2 = np.clip([by1, by2], 0, h0 - 1)
            if bx2 - bx1 < 2 or by2 - by1 < 2:
                continue
            results.append((float(bx1), float(by1), float(bx2), float(by2),
                             int(cls_ids[i]), float(scores[i])))
        return results


# ---------------------------------------------------------------------------
# Editable box item
# ---------------------------------------------------------------------------
class BoxItem(QGraphicsRectItem):
    MARGIN = 7   # px tolerance around an edge that counts as a resize grab
    HANDLE = 5   # half-size of the drawn handle squares

    def __init__(self, rect, class_id, main_window):
        super().__init__(rect)
        self.class_id = class_id
        self.mw = main_window
        # Note: intentionally NOT ItemIsMovable. Qt's built-in move drags the
        # item via setPos() while rect() stays untouched, which desynced the
        # saved coordinates from what you saw on screen. Move/resize are both
        # handled manually below by editing rect() directly, so rect() is
        # always the true, current, scene-space box.
        self.setFlags(QGraphicsRectItem.ItemIsSelectable | QGraphicsRectItem.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self._resize_dir = None
        self._press_rect = None
        self._press_scene = None
        self.refresh_style()

    def color(self):
        return QColor(COLORS[self.class_id % len(COLORS)])

    def refresh_style(self):
        c = self.color()
        pen = QPen(c, 3 if self.isSelected() else 2)
        self.setPen(pen)
        b = QColor(c); b.setAlpha(35)
        self.setBrush(QBrush(b))

    def class_name(self):
        names = self.mw.class_names
        return names[self.class_id] if 0 <= self.class_id < len(names) else f"cls{self.class_id}"

    def boundingRect(self):
        # paint() draws the class label above the rect and resize handles
        # past its corners; the default QGraphicsRectItem boundingRect() only
        # covers rect()+pen width, so Qt's dirty-region tracking never clears
        # those extra pixels on move/resize, leaving ghost trails behind.
        # Padding generously here keeps every repaint fully invalidated.
        pad = self.HANDLE + 6
        r = QRectF(self.rect()).adjusted(-pad, -pad - 18, pad, pad)
        return r

    def _edge_flags(self, pos):
        r = self.rect()
        m = self.MARGIN
        return (abs(pos.x() - r.left()) < m, abs(pos.x() - r.right()) < m,
                abs(pos.y() - r.top()) < m, abs(pos.y() - r.bottom()) < m)

    def hoverMoveEvent(self, event):
        l, ri, t, b = self._edge_flags(event.pos())
        if (l or ri) and (t or b):
            self.setCursor(Qt.SizeFDiagCursor if l == t else Qt.SizeBDiagCursor)
        elif l or ri:
            self.setCursor(Qt.SizeHorCursor)
        elif t or b:
            self.setCursor(Qt.SizeVerCursor)
        else:
            self.setCursor(Qt.SizeAllCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event):
        l, ri, t, b = self._edge_flags(event.pos())
        d = ("t" if t else "") + ("b" if b else "") + ("l" if l else "") + ("r" if ri else "")
        self._resize_dir = d or None   # empty -> plain body drag = move
        self._press_rect = QRectF(self.rect())
        self._press_scene = event.scenePos()
        self.mw.select_box(self)
        super().mousePressEvent(event)  # only handles selection now (not movable)

    def mouseMoveEvent(self, event):
        if self._press_scene is None:
            return
        delta = event.scenePos() - self._press_scene
        r = QRectF(self._press_rect)
        if self._resize_dir:
            if "l" in self._resize_dir: r.setLeft(r.left() + delta.x())
            if "r" in self._resize_dir: r.setRight(r.right() + delta.x())
            if "t" in self._resize_dir: r.setTop(r.top() + delta.y())
            if "b" in self._resize_dir: r.setBottom(r.bottom() + delta.y())
            self.setRect(r.normalized())
        else:
            self.setRect(r.translated(delta.x(), delta.y()))
        self.mw.mark_dirty()
        self.mw.sync_box_panel()

    def mouseReleaseEvent(self, event):
        self._resize_dir = None
        self._press_rect = None
        self._press_scene = None
        super().mouseReleaseEvent(event)
        self.mw.mark_dirty()
        self.mw.sync_box_panel()
        self.mw.render_annotation_list()

    def paint(self, painter, option, widget=None):
        self.refresh_style()
        option.state &= ~option.state.State_Selected  # suppress Qt's default dashed box
        super().paint(painter, option, widget)
        painter.setPen(self.color())
        painter.setFont(QFont("Arial", 10, QFont.Bold))
        painter.drawText(self.rect().topLeft() + QPointF(2, -4), self.class_name())
        if self.isSelected():
            r = self.rect()
            handle_pts = [
                r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight(),
                QPointF(r.center().x(), r.top()), QPointF(r.center().x(), r.bottom()),
                QPointF(r.left(), r.center().y()), QPointF(r.right(), r.center().y()),
            ]
            painter.setBrush(QBrush(QColor("#ffffff")))
            painter.setPen(QPen(self.color(), 1.5))
            hs = self.HANDLE
            for p in handle_pts:
                painter.drawRect(QRectF(p.x() - hs, p.y() - hs, hs * 2, hs * 2))


# ---------------------------------------------------------------------------
# Scene that supports drag-to-draw new boxes and click-empty-space to deselect
# ---------------------------------------------------------------------------
class CanvasScene(QGraphicsScene):
    def __init__(self, mw):
        super().__init__()
        self.mw = mw
        self.drawing = False
        self.start = None
        self.temp = None

    def mousePressEvent(self, event):
        clicked = self.itemAt(event.scenePos(), self.mw.view.transform())
        if self.mw.draw_mode and event.button() == Qt.LeftButton and not isinstance(clicked, BoxItem):
            self.drawing = True
            self.start = event.scenePos()
            self.temp = QGraphicsRectItem(QRectF(self.start, self.start))
            self.temp.setPen(QPen(QColor("#00e5ff"), 2, Qt.DashLine))
            self.addItem(self.temp)
            return
        if event.button() == Qt.LeftButton and not isinstance(clicked, BoxItem):
            self.mw.select_box(None)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drawing and self.temp:
            self.temp.setRect(QRectF(self.start, event.scenePos()).normalized())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.drawing:
            self.drawing = False
            rect = self.temp.rect()
            self.removeItem(self.temp)
            self.temp = None
            if rect.width() > 4 and rect.height() > 4:
                box = self.mw.add_box(rect)
                self.mw.select_box(box)
            return
        super().mouseReleaseEvent(event)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ONNX-Assisted Labeling Tool")
        self.resize(1400, 860)

        self.folder = None
        self.image_paths = []
        self.current_index = -1
        self.class_names = []
        self.detector = None
        self.draw_mode = False
        self.selected_box = None
        self.pixmap_item = None
        self.dirty = False
        self.view_scale = 1.0

        self._build_ui()
        self._build_shortcuts()
        self.set_tool(self.draw_mode)

    # ---- UI construction ----
    def _build_ui(self):
        central = QWidget(); self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_header())

        workspace = QWidget()
        ws_layout = QHBoxLayout(workspace)
        ws_layout.setContentsMargins(0, 0, 0, 0)
        ws_layout.setSpacing(0)
        ws_layout.addWidget(self._build_sidebar())
        ws_layout.addWidget(self._build_canvas_area(), 1)
        ws_layout.addWidget(self._build_right_panel())
        outer.addWidget(workspace, 1)

        self.status = QStatusBar(); self.setStatusBar(self.status)

    def _build_header(self):
        header = QWidget(); header.setObjectName("header")
        header.setFixedHeight(48)
        lay = QHBoxLayout(header)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(8)

        title = QLabel("ONNX LABEL STUDIO")
        title.setProperty("role", "title")
        lay.addWidget(title)
        lay.addSpacing(16)

        btn_folder = QPushButton("Open Image Folder")
        btn_folder.clicked.connect(self.open_folder)
        lay.addWidget(btn_folder)
        btn_model = QPushButton("Load ONNX Model")
        btn_model.clicked.connect(self.load_model)
        lay.addWidget(btn_model)
        self.lbl_model = QLabel("Model: none")
        self.lbl_model.setProperty("role", "hint")
        lay.addWidget(self.lbl_model)

        lay.addStretch()
        btn_save = QPushButton("Save Labels (S)")
        btn_save.setProperty("variant", "primary")
        btn_save.clicked.connect(self.save_current_labels)
        lay.addWidget(btn_save)
        return header

    def _build_sidebar(self):
        sidebar = QWidget(); sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(260)
        lay = QVBoxLayout(sidebar)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        cls_title = QLabel("CLASSES")
        cls_title.setProperty("role", "section")
        cls_title.setContentsMargins(10, 8, 10, 0)
        lay.addWidget(cls_title)

        self.class_palette_area = QScrollArea()
        self.class_palette_area.setWidgetResizable(True)
        self.class_palette_area.setFixedHeight(220)
        self.class_palette_area.setFrameShape(QFrame.NoFrame)
        palette_host = QWidget()
        self.class_palette_layout = QVBoxLayout(palette_host)
        self.class_palette_layout.setContentsMargins(6, 4, 6, 4)
        self.class_palette_layout.setSpacing(2)
        self.class_palette_layout.addStretch()
        self.class_palette_area.setWidget(palette_host)
        lay.addWidget(self.class_palette_area)

        add_row = QWidget()
        add_lay = QHBoxLayout(add_row)
        add_lay.setContentsMargins(8, 4, 8, 8)
        self.new_class_input = QLineEdit()
        self.new_class_input.setPlaceholderText("Add class…")
        self.new_class_input.returnPressed.connect(self.add_class_from_input)
        add_btn = QPushButton("+")
        add_btn.setFixedWidth(28)
        add_btn.clicked.connect(self.add_class_from_input)
        add_lay.addWidget(self.new_class_input)
        add_lay.addWidget(add_btn)
        lay.addWidget(add_row)

        img_title = QLabel("IMAGES")
        img_title.setProperty("role", "section")
        img_title.setContentsMargins(10, 8, 10, 0)
        lay.addWidget(img_title)
        self.file_list = QListWidget()
        self.file_list.currentRowChanged.connect(self.on_file_selected)
        lay.addWidget(self.file_list, 1)

        # Hidden state holder: not shown, just backs "active class for new
        # boxes" + drives reclassify-selected-box via currentIndexChanged.
        self.class_combo = QComboBox()
        self.class_combo.currentIndexChanged.connect(self.reclass_selected_if_any)

        return sidebar

    def _build_canvas_area(self):
        area = QWidget()
        lay = QVBoxLayout(area)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        toolbar = QWidget(); toolbar.setObjectName("toolbar")
        tb = QHBoxLayout(toolbar)
        tb.setContentsMargins(10, 6, 10, 6)
        tb.setSpacing(6)
        self.btn_tool_box = QPushButton("▭ Box (N)")
        self.btn_tool_box.setCheckable(True)
        self.btn_tool_box.clicked.connect(lambda: self.set_tool(True))
        self.btn_tool_select = QPushButton("◱ Select")
        self.btn_tool_select.setCheckable(True)
        self.btn_tool_select.clicked.connect(lambda: self.set_tool(False))
        tb.addWidget(self.btn_tool_box)
        tb.addWidget(self.btn_tool_select)
        sep = QFrame(); sep.setFrameShape(QFrame.VLine)
        tb.addWidget(sep)
        btn_zoom_out = QPushButton("−"); btn_zoom_out.setFixedWidth(32)
        btn_zoom_out.clicked.connect(lambda: self.zoom_view(0.8))
        btn_zoom_in = QPushButton("+"); btn_zoom_in.setFixedWidth(32)
        btn_zoom_in.clicked.connect(lambda: self.zoom_view(1.25))
        btn_zoom_fit = QPushButton("Fit")
        btn_zoom_fit.clicked.connect(self.fit_view)
        tb.addWidget(btn_zoom_out); tb.addWidget(btn_zoom_in); tb.addWidget(btn_zoom_fit)
        sep2 = QFrame(); sep2.setFrameShape(QFrame.VLine)
        tb.addWidget(sep2)
        btn_del_box = QPushButton("Delete Box (Del)")
        btn_del_box.setProperty("variant", "danger")
        btn_del_box.clicked.connect(self.delete_selected_box)
        tb.addWidget(btn_del_box)
        tb.addStretch()
        self.lbl_zoom = QLabel("100%")
        self.lbl_zoom.setProperty("role", "hint")
        tb.addWidget(self.lbl_zoom)
        lay.addWidget(toolbar)

        nav = QWidget(); nav.setObjectName("navBar")
        nav_lay = QHBoxLayout(nav)
        nav_lay.setContentsMargins(8, 4, 8, 4)
        btn_prev = QPushButton("◀"); btn_prev.setFixedWidth(32)
        btn_prev.clicked.connect(lambda: self.step_image(-1))
        btn_next = QPushButton("▶"); btn_next.setFixedWidth(32)
        btn_next.clicked.connect(lambda: self.step_image(1))
        self.lbl_nav = QLabel("– / –")
        self.lbl_nav.setAlignment(Qt.AlignCenter)
        self.lbl_nav.setProperty("role", "hint")
        nav_lay.addWidget(btn_prev)
        nav_lay.addWidget(self.lbl_nav, 1)
        nav_lay.addWidget(btn_next)
        lay.addWidget(nav)

        self.scene = CanvasScene(self)
        self.view = QGraphicsView(self.scene)
        self.view.setRenderHints(self.view.renderHints())
        self.view.setDragMode(QGraphicsView.NoDrag)
        lay.addWidget(self.view, 1)

        stats = QWidget(); stats.setObjectName("statsBar")
        st = QHBoxLayout(stats)
        st.setContentsMargins(12, 4, 12, 4)
        st.setSpacing(16)
        self.stat_img = QLabel("–")
        self.stat_size = QLabel("–")
        self.stat_ann = QLabel("Anns: 0")
        self.stat_class = QLabel("Class: –")
        self.stat_zoom = QLabel("Zoom: 100%")
        for w in (self.stat_img, self.stat_size, self.stat_ann, self.stat_class, self.stat_zoom):
            w.setProperty("role", "hint")
            st.addWidget(w)
        st.addStretch()
        lay.addWidget(stats)

        return area

    def _build_right_panel(self):
        panel = QWidget(); panel.setObjectName("rightPanel")
        panel.setFixedWidth(300)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        ann_title = QLabel("ANNOTATIONS")
        ann_title.setProperty("role", "section")
        ann_title.setContentsMargins(10, 8, 10, 0)
        lay.addWidget(ann_title)
        self.annot_area = QScrollArea()
        self.annot_area.setWidgetResizable(True)
        self.annot_area.setFixedHeight(200)
        self.annot_area.setFrameShape(QFrame.NoFrame)
        annot_host = QWidget()
        self.annot_list_layout = QVBoxLayout(annot_host)
        self.annot_list_layout.setContentsMargins(6, 4, 6, 4)
        self.annot_list_layout.setSpacing(2)
        self.annot_list_layout.addStretch()
        self.annot_area.setWidget(annot_host)
        lay.addWidget(self.annot_area)

        box_title = QLabel("SELECTED BOX")
        box_title.setProperty("role", "section")
        box_title.setContentsMargins(10, 8, 10, 0)
        lay.addWidget(box_title)
        box_form_host = QWidget()
        box_form = QFormLayout(box_form_host)
        box_form.setContentsMargins(10, 4, 10, 6)
        self.spin_x = self._make_spin()
        self.spin_y = self._make_spin()
        self.spin_w = self._make_spin()
        self.spin_h = self._make_spin()
        box_form.addRow("X", self.spin_x)
        box_form.addRow("Y", self.spin_y)
        box_form.addRow("W", self.spin_w)
        box_form.addRow("H", self.spin_h)
        lay.addWidget(box_form_host)
        hint = QLabel("Drag body to move, drag a handle to resize.\n"
                       "Arrows nudge 1px (Shift 10px). 1-9 sets the\n"
                       "active class / reclasses selection. Esc deselects.")
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        hint.setContentsMargins(10, 0, 10, 8)
        lay.addWidget(hint)
        self.set_box_panel_enabled(False)

        al_title = QLabel("AUTO-LABEL (ONNX)")
        al_title.setProperty("role", "section")
        al_title.setContentsMargins(10, 8, 10, 0)
        lay.addWidget(al_title)
        al_host = QWidget()
        al_lay = QVBoxLayout(al_host)
        al_lay.setContentsMargins(10, 2, 10, 6)
        self.conf_slider = self._slider("Confidence", 25)
        self.iou_slider = self._slider("IoU (NMS)", 45)
        al_lay.addWidget(self.conf_slider[0]); al_lay.addWidget(self.conf_slider[1])
        al_lay.addWidget(self.iou_slider[0]); al_lay.addWidget(self.iou_slider[1])
        btn_auto_cur = QPushButton("▶ Auto-Label Current Image")
        btn_auto_cur.clicked.connect(self.auto_label_current)
        al_lay.addWidget(btn_auto_cur)
        btn_auto_all = QPushButton("▶▶ Auto-Label Remaining Unlabeled")
        btn_auto_all.clicked.connect(self.auto_label_folder)
        al_lay.addWidget(btn_auto_all)
        lay.addWidget(al_host)

        lay.addStretch()
        return panel

    def _make_spin(self):
        s = QDoubleSpinBox()
        s.setRange(0, 100000)
        s.setDecimals(1)
        s.setSingleStep(1.0)
        s.valueChanged.connect(self.apply_box_panel)
        return s

    def _slider(self, name, default):
        lbl = QLabel(f"{name}: {default/100:.2f}")
        s = QSlider(Qt.Horizontal); s.setRange(1, 99); s.setValue(default)
        s.valueChanged.connect(lambda v, n=name, l=lbl: l.setText(f"{n}: {v/100:.2f}"))
        return lbl, s

    def _build_shortcuts(self):
        QShortcut(QKeySequence("D"), self, activated=lambda: self.step_image(1))
        QShortcut(QKeySequence("A"), self, activated=lambda: self.step_image(-1))
        # Arrow keys: nudge the selected box if one is selected, otherwise
        # Left/Right navigate between images.
        QShortcut(QKeySequence("Left"), self, activated=lambda: self.nudge_or_navigate(-1, 0, -1))
        QShortcut(QKeySequence("Right"), self, activated=lambda: self.nudge_or_navigate(1, 0, 1))
        QShortcut(QKeySequence("Up"), self, activated=lambda: self.nudge_or_navigate(0, -1, 0))
        QShortcut(QKeySequence("Down"), self, activated=lambda: self.nudge_or_navigate(0, 1, 0))
        QShortcut(QKeySequence("Shift+Left"), self, activated=lambda: self.nudge_or_navigate(-10, 0, 0))
        QShortcut(QKeySequence("Shift+Right"), self, activated=lambda: self.nudge_or_navigate(10, 0, 0))
        QShortcut(QKeySequence("Shift+Up"), self, activated=lambda: self.nudge_or_navigate(0, -10, 0))
        QShortcut(QKeySequence("Shift+Down"), self, activated=lambda: self.nudge_or_navigate(0, 10, 0))
        QShortcut(QKeySequence("Delete"), self, activated=self.delete_selected_box)
        QShortcut(QKeySequence("Backspace"), self, activated=self.delete_selected_box)
        QShortcut(QKeySequence("S"), self, activated=self.save_current_labels)
        QShortcut(QKeySequence("N"), self, activated=self.toggle_draw_mode)
        QShortcut(QKeySequence("Escape"), self, activated=lambda: self.select_box(None))
        for n in range(1, 10):
            QShortcut(QKeySequence(str(n)), self, activated=lambda n=n: self.select_class_by_number(n))

    # ---- folder / model loading ----
    def open_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select image folder")
        if not folder:
            return
        self.save_current_labels()
        self.folder = folder
        self.image_paths = sorted(
            p for p in glob.glob(os.path.join(folder, "*")) if p.lower().endswith(IMG_EXTS)
        )
        self.file_list.clear()
        for p in self.image_paths:
            self.file_list.addItem(QListWidgetItem(os.path.basename(p)))

        classes_file = os.path.join(folder, "classes.txt")
        if os.path.exists(classes_file):
            with open(classes_file) as f:
                self.class_names = [l.strip() for l in f if l.strip()]
            self.refresh_class_widgets()

        if self.image_paths:
            self.file_list.setCurrentRow(0)
        self.update_nav_counter()
        self.status.showMessage(f"Loaded {len(self.image_paths)} images from {folder}")

    def load_model(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select ONNX model", "", "ONNX (*.onnx)")
        if not path:
            return
        try:
            self.detector = OnnxDetector(path)
            self.lbl_model.setText(f"Model: {os.path.basename(path)} ({self.detector.input_size[1]}x{self.detector.input_size[0]})")
            self.status.showMessage("Model loaded.")
        except Exception as e:
            QMessageBox.critical(self, "Error loading model", str(e))

    # ---- classes ----
    def add_class_from_input(self):
        name = self.new_class_input.text().strip()
        if name:
            self._create_class(name)
            self.new_class_input.clear()

    def add_class(self):
        name, ok = QInputDialog.getText(self, "Add class", "Class name:")
        if ok and name.strip():
            self._create_class(name.strip())

    def _create_class(self, name):
        if name in self.class_names:
            return
        self.class_names.append(name)
        self.refresh_class_widgets()
        self.save_classes_file()

    def remove_class_at(self, index):
        if not (0 <= index < len(self.class_names)):
            return
        resp = QMessageBox.question(
            self, "Remove class", f'Remove class "{self.class_names[index]}"?',
            QMessageBox.Yes | QMessageBox.No
        )
        if resp != QMessageBox.Yes:
            return
        del self.class_names[index]
        self.refresh_class_widgets()
        self.save_classes_file()

    def set_active_class(self, index):
        if not (0 <= index < len(self.class_names)):
            return
        self.class_combo.setCurrentIndex(index)
        self._render_class_palette()
        self.update_stats_bar()

    def select_class_by_number(self, n):
        idx = n - 1
        if not (0 <= idx < len(self.class_names)):
            return
        self.set_active_class(idx)
        if self.selected_box is not None:
            self.render_annotation_list()

    def refresh_class_widgets(self):
        self.class_combo.blockSignals(True)
        self.class_combo.clear()
        for n in self.class_names:
            self.class_combo.addItem(n)
        self.class_combo.blockSignals(False)
        self._render_class_palette()
        self.render_annotation_list()
        self.update_stats_bar()

    def _render_class_palette(self):
        layout = self.class_palette_layout
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        active = self.class_combo.currentIndex()
        for i, name in enumerate(self.class_names):
            color = COLORS[i % len(COLORS)]
            key_num = i + 1 if i < 9 else None
            row = ClassRow(i, name, color, key_num, i == active,
                            self.set_active_class, self.remove_class_at)
            layout.addWidget(row)
        layout.addStretch()

    def save_classes_file(self):
        if self.folder:
            with open(os.path.join(self.folder, "classes.txt"), "w") as f:
                f.write("\n".join(self.class_names))

    # ---- image navigation ----
    def step_image(self, delta):
        if not self.image_paths:
            return
        self.save_current_labels()
        new_row = max(0, min(len(self.image_paths) - 1, self.current_index + delta))
        self.file_list.setCurrentRow(new_row)

    def on_file_selected(self, row):
        if row < 0 or row >= len(self.image_paths):
            return
        self.current_index = row
        self.load_image(self.image_paths[row])

    def load_image(self, path):
        self.scene.clear()
        self.pixmap_item = None  # the item above was just destroyed by clear()
        self.select_box(None)
        img = cv2.imread(path)
        if img is None:
            self.status.showMessage(f"Could not read {path}")
            return
        h, w = img.shape[:2]
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888)
        pix = QPixmap.fromImage(qimg)
        self.pixmap_item = QGraphicsPixmapItem(pix)
        self.scene.addItem(self.pixmap_item)
        self.scene.setSceneRect(0, 0, w, h)
        self.fit_view()

        label_path = self._label_path(path)
        if os.path.exists(label_path):
            self._load_yolo_labels(label_path, w, h)
        self.dirty = False
        self.render_annotation_list()
        self.update_nav_counter()
        self.update_stats_bar()
        self.status.showMessage(f"[{self.current_index+1}/{len(self.image_paths)}] {os.path.basename(path)}  ({w}x{h})")

    def update_nav_counter(self):
        if self.image_paths:
            self.lbl_nav.setText(f"{self.current_index + 1} / {len(self.image_paths)}")
        else:
            self.lbl_nav.setText("– / –")

    # ---- zoom ----
    def set_tool(self, is_box):
        self.draw_mode = is_box
        self.btn_tool_box.setChecked(is_box)
        self.btn_tool_select.setChecked(not is_box)
        self.view.setCursor(Qt.CrossCursor if is_box else Qt.ArrowCursor)

    def zoom_view(self, factor):
        if self.pixmap_item is None:
            return
        self.view.scale(factor, factor)
        self.view_scale *= factor
        self._update_zoom_labels()

    def fit_view(self):
        if self.pixmap_item is None:
            return
        self.view.resetTransform()
        self.view.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)
        self.view_scale = self.view.transform().m11()
        self._update_zoom_labels()

    def _update_zoom_labels(self):
        pct = f"{round(self.view_scale * 100)}%"
        self.lbl_zoom.setText(pct)
        self.stat_zoom.setText(f"Zoom: {pct}")

    # ---- stats / annotation list ----
    def update_stats_bar(self):
        if self.current_index >= 0 and self.pixmap_item is not None:
            path = self.image_paths[self.current_index]
            w, h = self.pixmap_item.pixmap().width(), self.pixmap_item.pixmap().height()
            self.stat_img.setText(os.path.basename(path))
            self.stat_size.setText(f"{w}×{h}")
        else:
            self.stat_img.setText("–")
            self.stat_size.setText("–")
        boxes = [it for it in self.scene.items() if isinstance(it, BoxItem)] if self.pixmap_item else []
        self.stat_ann.setText(f"Anns: {len(boxes)}")
        active = self.class_combo.currentIndex()
        self.stat_class.setText(
            f"Class: {self.class_names[active]}" if 0 <= active < len(self.class_names) else "Class: –"
        )
        self._update_zoom_labels()

    def render_annotation_list(self):
        layout = self.annot_list_layout
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        boxes = [it for it in self.scene.items() if isinstance(it, BoxItem)]
        boxes.reverse()  # scene.items() is topmost-first; show draw order instead
        for box in boxes:
            row = AnnotationRow(box, self.class_names, box is self.selected_box,
                                 self.select_box, self._delete_box)
            layout.addWidget(row)
        layout.addStretch()
        self.stat_ann.setText(f"Anns: {len(boxes)}")

    def _delete_box(self, box):
        self.scene.removeItem(box)
        if self.selected_box is box:
            self.select_box(None)
        else:
            self.mark_dirty()
            self.render_annotation_list()
            self.update_stats_bar()

    def _label_path(self, image_path):
        return os.path.splitext(image_path)[0] + ".txt"

    def _load_yolo_labels(self, label_path, w, h):
        with open(label_path) as f:
            for line in f:
                parts = line.split()
                if len(parts) < 5:
                    continue
                cid, cx, cy, bw, bh = int(parts[0]), *map(float, parts[1:5])
                x1 = (cx - bw / 2) * w; y1 = (cy - bh / 2) * h
                x2 = (cx + bw / 2) * w; y2 = (cy + bh / 2) * h
                self.add_box(QRectF(QPointF(x1, y1), QPointF(x2, y2)), cid, mark_dirty=False)

    # ---- boxes ----
    def add_box(self, rect, class_id=None, mark_dirty=True):
        if class_id is None:
            class_id = self.class_combo.currentIndex()
            if class_id < 0:
                if not self.class_names:
                    self.add_class()
                class_id = max(0, self.class_combo.currentIndex())
        box = BoxItem(rect, class_id, self)
        self.scene.addItem(box)
        if mark_dirty:
            self.mark_dirty()
        return box

    def select_box(self, box):
        for it in self.scene.items():
            if isinstance(it, BoxItem):
                it.setSelected(it is box)
        self.selected_box = box
        self.set_box_panel_enabled(box is not None)
        if box is not None:
            self.class_combo.blockSignals(True)
            self.class_combo.setCurrentIndex(box.class_id)
            self.class_combo.blockSignals(False)
        self.sync_box_panel()
        self._render_class_palette()
        self.render_annotation_list()
        self.update_stats_bar()

    def set_box_panel_enabled(self, enabled):
        for w in (self.spin_x, self.spin_y, self.spin_w, self.spin_h):
            w.setEnabled(enabled)

    def sync_box_panel(self):
        """Refresh the X/Y/W/H spinboxes from the selected box's live geometry."""
        for w in (self.spin_x, self.spin_y, self.spin_w, self.spin_h):
            w.blockSignals(True)
        if self.selected_box is not None:
            r = self.selected_box.rect()
            self.spin_x.setValue(r.x())
            self.spin_y.setValue(r.y())
            self.spin_w.setValue(r.width())
            self.spin_h.setValue(r.height())
        else:
            for w in (self.spin_x, self.spin_y, self.spin_w, self.spin_h):
                w.setValue(0)
        for w in (self.spin_x, self.spin_y, self.spin_w, self.spin_h):
            w.blockSignals(False)

    def apply_box_panel(self):
        """Apply typed X/Y/W/H spinbox values to the selected box."""
        if self.selected_box is None:
            return
        r = QRectF(self.spin_x.value(), self.spin_y.value(),
                   max(1.0, self.spin_w.value()), max(1.0, self.spin_h.value()))
        self.selected_box.setRect(r)
        self.mark_dirty()
        self.render_annotation_list()

    def nudge_or_navigate(self, dx, dy, nav_dir):
        if self.selected_box is not None:
            r = self.selected_box.rect()
            self.selected_box.setRect(r.translated(dx, dy))
            self.mark_dirty()
            self.sync_box_panel()
            self.render_annotation_list()
        elif nav_dir != 0:
            self.step_image(nav_dir)

    def reclass_selected_if_any(self, idx):
        if self.selected_box is not None and idx >= 0:
            self.selected_box.class_id = idx
            self.selected_box.refresh_style()
            self.mark_dirty()
            self.render_annotation_list()

    def delete_selected_box(self):
        if self.selected_box is not None:
            self.scene.removeItem(self.selected_box)
            self.select_box(None)
            self.mark_dirty()

    def toggle_draw_mode(self):
        self.set_tool(not self.draw_mode)

    def mark_dirty(self):
        self.dirty = True

    # ---- saving ----
    def save_current_labels(self):
        if self.current_index < 0 or self.pixmap_item is None:
            return
        path = self.image_paths[self.current_index]
        w, h = self.pixmap_item.pixmap().width(), self.pixmap_item.pixmap().height()
        lines = []
        for it in self.scene.items():
            if isinstance(it, BoxItem):
                r = it.rect()
                cx = (r.left() + r.right()) / 2 / w
                cy = (r.top() + r.bottom()) / 2 / h
                bw = r.width() / w
                bh = r.height() / h
                lines.append(f"{it.class_id} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
        with open(self._label_path(path), "w") as f:
            f.write("\n".join(lines))
        self.dirty = False

    # ---- auto-labeling ----
    def auto_label_current(self):
        if self.detector is None:
            QMessageBox.warning(self, "No model", "Load an ONNX model first.")
            return
        if self.current_index < 0:
            return
        self._run_auto_label(self.image_paths[self.current_index], replace_current_scene=True)
        self.status.showMessage("Auto-labeled current image.")

    def auto_label_folder(self):
        if self.detector is None:
            QMessageBox.warning(self, "No model", "Load an ONNX model first.")
            return
        if not self.image_paths:
            return
        self.save_current_labels()
        count = 0
        for path in self.image_paths:
            lbl = self._label_path(path)
            if os.path.exists(lbl) and os.path.getsize(lbl) > 0:
                continue  # skip already-labeled images (manual work preserved)
            self._run_auto_label(path, replace_current_scene=False)
            count += 1
        self.status.showMessage(f"Auto-labeled {count} previously unlabeled images.")
        if self.current_index >= 0:
            self.load_image(self.image_paths[self.current_index])

    def _run_auto_label(self, path, replace_current_scene):
        img = cv2.imread(path)
        if img is None:
            return
        conf = self.conf_slider[1].value() / 100.0
        iou = self.iou_slider[1].value() / 100.0
        nc = len(self.class_names) if self.class_names else None
        dets = self.detector.infer(img, conf_thres=conf, iou_thres=iou, num_classes=nc)
        h, w = img.shape[:2]
        with open(self._label_path(path), "w") as f:
            lines = []
            for x1, y1, x2, y2, cid, score in dets:
                cx = (x1 + x2) / 2 / w; cy = (y1 + y2) / 2 / h
                bw = (x2 - x1) / w; bh = (y2 - y1) / h
                lines.append(f"{cid} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
            f.write("\n".join(lines))
        if replace_current_scene and path == self.image_paths[self.current_index]:
            self.load_image(path)

    def closeEvent(self, event):
        self.save_current_labels()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_QSS)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()