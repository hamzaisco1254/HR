"""Generate sample header and footer images for documents"""
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

def create_logo_image():
    """Create Planisware logo image"""
    # Create image
    img = Image.new('RGB', (300, 100), color='white')
    draw = ImageDraw.Draw(img)
    
    # Draw simple crescent moon shape (approximated with arc)
    draw.arc([30, 20, 70, 60], 0, 360, fill='#E75BA7', width=3)
    
    # Add text
    # Using default font since we may not have specific fonts
    try:
        font = ImageFont.truetype("arial.ttf", 28)
    except:
        font = ImageFont.load_default()
    
    draw.text((90, 30), "Planisware", fill='#999999', font=font)
    
    # Save image
    output_path = Path("templates/planisware_logo.png")
    img.save(str(output_path))
    print(f"✓ Logo image created: {output_path}")
    return str(output_path)

def create_footer_image():
    """Create company info footer image"""
    # Create image with company details
    img = Image.new('RGB', (600, 120), color='white')
    draw = ImageDraw.Draw(img)
    
    try:
        font_large = ImageFont.truetype("arial.ttf", 11)
        font_small = ImageFont.truetype("arial.ttf", 9)
    except:
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()
    
    # Add company information
    text_lines = [
        "PLW Tunisia SARL",
        "56, Boulevard de la Corniche Avenue Béji Caïd Essebsi Les Jardins du Lac, Lac2,1053 Tunis",
        "MF :1692096/F/A/M/000. Tel : +216 31 400 460",
        "Courriel : admin_tun@planisware.com"
    ]
    
    y_pos = 10
    for line in text_lines:
        draw.text((10, y_pos), line, fill='#999999', font=font_small)
        y_pos += 25
    
    # Save image
    output_path = Path("templates/footer_info.png")
    img.save(str(output_path))
    print(f"✓ Footer image created: {output_path}")
    return str(output_path)

if __name__ == "__main__":
    Path("templates").mkdir(parents=True, exist_ok=True)
    create_logo_image()
    create_footer_image()
    print("✅ All images created successfully!")
