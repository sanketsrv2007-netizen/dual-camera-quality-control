
import cv2
import numpy as np

# ==============================
# CALIBRATION
# ==============================
REAL_WIDTH_MM = 100.0
REAL_HEIGHT_MM = 60.0

TARGET_WIDTH = 100.0
TARGET_HEIGHT = 60.0

WIDTH_TOL = 2.0
HEIGHT_TOL = 2.0

points = []
measurement = None

def order_points(pts):
    pts = np.array(pts, dtype="float32")
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1)
    return np.array([
        pts[np.argmin(s)],
        pts[np.argmin(d)],
        pts[np.argmax(s)],
        pts[np.argmax(d)]
    ], dtype="float32")

def dist(a,b):
    return np.linalg.norm(a-b)

def mouse(event,x,y,flags,param):
    global points
    if event == cv2.EVENT_LBUTTONDOWN:
        if len(points) < 4:
            points.append((x,y))

cap = cv2.VideoCapture(0)
if not cap.isOpened():
    raise RuntimeError("Could not open camera")

cv2.namedWindow("Inspection")
cv2.setMouseCallback("Inspection", mouse)

while True:
    ret, frame = cap.read()
    if not ret:
        break

    display = frame.copy()

    for p in points:
        cv2.circle(display, p, 5, (0,0,255), -1)

    if len(points) > 1:
        cv2.polylines(display,[np.array(points,np.int32)],False,(255,0,0),2)

    if len(points) == 4:
        ordered = order_points(points)
        cv2.polylines(display,[ordered.astype(int)],True,(0,255,0),2)

        tl,tr,br,bl = ordered

        w = (dist(tl,tr)+dist(bl,br))/2
        h = (dist(tl,bl)+dist(tr,br))/2

        ppm_w = w/REAL_WIDTH_MM
        ppm_h = h/REAL_HEIGHT_MM
        ppm = (ppm_w+ppm_h)/2

        width_mm = w/ppm
        height_mm = h/ppm
        area_px = cv2.contourArea(ordered)
        area_mm = area_px/(ppm*ppm)

        ok_w = abs(width_mm-TARGET_WIDTH)<=WIDTH_TOL
        ok_h = abs(height_mm-TARGET_HEIGHT)<=HEIGHT_TOL
        status = "PASS" if ok_w and ok_h else "FAIL"
        color = (0,255,0) if status=="PASS" else (0,0,255)

        info = [
            f"PPM: {ppm:.3f}",
            f"Width : {width_mm:.2f} mm",
            f"Height: {height_mm:.2f} mm",
            f"Area  : {area_mm:.2f} mm2",
            f"Status: {status}"
        ]
        y=30
        for t in info:
            cv2.putText(display,t,(10,y),cv2.FONT_HERSHEY_SIMPLEX,0.7,color if "Status" in t else (255,255,255),2)
            y+=30

        measurement = display.copy()

    cv2.putText(display,
                "LMB: 4 corners | R:Reset | S:Save | Q/Esc:Quit",
                (10,display.shape[0]-15),
                cv2.FONT_HERSHEY_SIMPLEX,0.55,(0,255,255),2)

    cv2.imshow("Inspection", display)

    key = cv2.waitKey(1) & 0xFF

    if key in (ord('q'),27):
        break
    elif key == ord('r'):
        points.clear()
        measurement = None
    elif key == ord('s') and measurement is not None:
        cv2.imwrite("inspection_result.png", measurement)
        print("Saved inspection_result.png")

cap.release()
cv2.destroyAllWindows()
