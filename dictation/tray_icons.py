import os
from PIL import Image, ImageDraw

def create_tray_image(color="green"):
    # Create 64x64 icon with smooth anti-aliased circle
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    
    color_map = {
        "idle": (34, 197, 94, 255),       # Emerald green
        "recording": (239, 68, 68, 255),   # Bright red
        "processing": (234, 179, 8, 255),  # Amber yellow
        "error": (156, 163, 175, 255)      # Gray
    }
    fill_color = color_map.get(color, (34, 197, 94, 255))
    
    # Outer circle border
    draw.ellipse((4, 4, size - 4, size - 4), outline=(255, 255, 255, 220), width=3)
    # Inner filled circle
    draw.ellipse((10, 10, size - 10, size - 10), fill=fill_color)
    
    # Draw simple mic glyph in center (white)
    # Capsule
    draw.rounded_rectangle((26, 18, 38, 36), radius=5, fill=(255, 255, 255, 255))
    # Base arc
    draw.arc((22, 24, 42, 42), start=0, end=180, fill=(255, 255, 255, 255), width=2)
    # Stem
    draw.line((32, 42, 32, 48), fill=(255, 255, 255, 255), width=2)
    # Base stand
    draw.line((26, 48, 38, 48), fill=(255, 255, 255, 255), width=2)
    
    return image

if __name__ == "__main__":
    img = create_tray_image("idle")
    img.save(os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon_idle.png"))
    img_rec = create_tray_image("recording")
    img_rec.save(os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon_recording.png"))
    print("Tray icons generated successfully.")
