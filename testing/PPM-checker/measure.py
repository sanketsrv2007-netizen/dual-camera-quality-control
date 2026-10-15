import cv2
import numpy as np
import math

# ======================================================
# Enter the REAL dimensions of your calibration object
# ======================================================
REAL_WIDTH_MM = 100.0
REAL_HEIGHT_MM = 60.0

points = []

# ------------------------------------------------------
def order_points(pts):
    pts = np.array(pts, dtype="float32")

    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1)

    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    tr = pts[np.argmin(diff)]
    bl = pts[np.argmax(diff)]

    return np.array([tl, tr, br, bl])

# ------------------------------------------------------
def distance(p1, p2):
    return np.linalg.norm(np.array(p1)-np.array(p2))

# ------------------------------------------------------
def mouse(event, x, y, flags, param):

    global points

    if event == cv2.EVENT_LBUTTONDOWN:

        if len(points) < 4:
            points.append((x,y))

# ------------------------------------------------------

img = cv2.imread("top.jpg")

if img is None:
    print("Image not found")
    exit()

original = img.copy()

while True:

    img = original.copy()

    for p in points:
        cv2.circle(img,p,6,(0,0,255),-1)

    if len(points)>1:
        cv2.polylines(img,[np.array(points)],False,(255,0,0),2)

    if len(points)==4:

        ordered = order_points(points)

        cv2.polylines(img,[ordered.astype(int)],True,(0,255,0),2)

        tl,tr,br,bl = ordered

        widthTop = distance(tl,tr)
        widthBottom = distance(bl,br)

        heightLeft = distance(tl,bl)
        heightRight = distance(tr,br)

        widthPixels = (widthTop+widthBottom)/2
        heightPixels = (heightLeft+heightRight)/2

        ppm_width = widthPixels/REAL_WIDTH_MM
        ppm_height = heightPixels/REAL_HEIGHT_MM

        ppm = (ppm_width+ppm_height)/2

        widthMM = widthPixels/ppm
        heightMM = heightPixels/ppm

        perimeterPixels = (
            distance(tl,tr)+
            distance(tr,br)+
            distance(br,bl)+
            distance(bl,tl)
        )

        areaPixels = cv2.contourArea(ordered.astype(np.float32))
        areaMM = areaPixels/(ppm*ppm)

        info = [
            f"PPM : {ppm:.3f}",
            f"Pixels/mm : {ppm:.3f}",
            f"Width(px): {widthPixels:.1f}",
            f"Height(px): {heightPixels:.1f}",
            f"Width(mm): {widthMM:.2f}",
            f"Height(mm): {heightMM:.2f}",
            f"Area(px2): {areaPixels:.1f}",
            f"Area(mm2): {areaMM:.1f}",
            f"Perimeter(px): {perimeterPixels:.1f}"
        ]

        y = 25

        for t in info:
            cv2.putText(img,t,(10,y),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.6,(0,255,0),2)
            y += 25

        print("\n========== Calibration ==========")
        print("Corners:")
        print("TL:",tl)
        print("TR:",tr)
        print("BR:",br)
        print("BL:",bl)

        print(f"Width Pixels : {widthPixels:.2f}")
        print(f"Height Pixels: {heightPixels:.2f}")

        print(f"PPM Width : {ppm_width:.4f}")
        print(f"PPM Height: {ppm_height:.4f}")

        print(f"Average PPM: {ppm:.4f}")

        print(f"Width mm : {widthMM:.2f}")
        print(f"Height mm: {heightMM:.2f}")

        print(f"Area Pixels : {areaPixels:.2f}")
        print(f"Area mm² : {areaMM:.2f}")

        print("===============================\n")

        points=[]

    cv2.imshow("Calibration",img)

    cv2.setMouseCallback("Calibration",mouse)

    key=cv2.waitKey(1)

    if key==27:
        break

cv2.destroyAllWindows()