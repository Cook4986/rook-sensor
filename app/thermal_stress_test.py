import os
import time
from ultralytics import YOLO
from picamera2 import Picamera2
import cv2

def get_temp():
    try:
        with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
            return float(f.read()) / 1000.0
    except:
        return 0.0

def main():
    print("🚀 Starting 1088px Thermal Stress Test (60 Seconds)...")
    print("Initializing YOLOv11n at native 1080p...")
    model = YOLO("yolo11n.pt")
    
    cam = Picamera2()
    cam.configure(cam.create_video_configuration(main={"size": (1920, 1080)}))
    cam.start()

    start_time = time.time()
    frame_count = 0
    inference_times = []

    try:
        while time.time() - start_time < 60:
            temp = get_temp()
            if temp > 80.0:
                print(f"\n🛑 CRITICAL THERMAL LIMIT REACHED: {temp}°C! Initiating Emergency Shutdown...")
                os.system("sudo shutdown -h now")
                break
                
            frame = cam.capture_array()
            if frame.shape[2] == 4:
                frame = frame[:, :, :3]
                
            t0 = time.time()
            # 1088 is the closest multiple of 32 to 1080
            results = model(frame, imgsz=1088, conf=0.45, verbose=False)
            infer_time = time.time() - t0
            
            inference_times.append(infer_time)
            frame_count += 1
            
            print(f"Frame {frame_count:02d}: Temp={temp:.1f}°C | Infer Time={infer_time*1000:.0f}ms")
            
    except KeyboardInterrupt:
        print("\nTest aborted by user.")
    finally:
        cam.stop()
        cam.close()

    if inference_times:
        avg_time = sum(inference_times) / len(inference_times)
        print(f"\n📊 Test Complete!")
        print(f"Total Frames Processed: {frame_count}")
        print(f"Average 1088px Inference Time: {avg_time*1000:.0f}ms")
        print(f"Final Peak Temperature: {get_temp():.1f}°C")

if __name__ == "__main__":
    main()
