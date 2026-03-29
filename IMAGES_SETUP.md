# Adding Custom Logo and Footer Images

## How to Use Your Custom Images

The application now supports header and footer images in all generated documents:

- **Header (Top Left):** Logo image
- **Footer (Bottom Right):** Company info image

### Step 1: Save Your Images

1. **Logo Image (Header):**
   - Save the Planisware logo as: `templates/planisware_logo.png`
   - Recommended size: 300x100 pixels
   - Format: PNG or JPG

2. **Company Info Image (Footer):**
   - Save the company contact info as: `templates/footer_info.png`
   - Recommended size: 600x120 pixels
   - Format: PNG or JPG

### Step 2: Replace Generated Placeholder Images

Default placeholder images are in `templates/`:
- `templates/planisware_logo.png` (current placeholder)
- `templates/footer_info.png` (current placeholder)

Simply replace these files with your actual images.

### Example File Structure

```
templates/
├── planisware_logo.png          # Your logo (top left in header)
└── footer_info.png              # Your company info (bottom right in footer)
```

### Image Recommendations

**Logo Image:**
- Width: 1.2 inches (about 100 pixels)
- Height: Proportional to width
- Best format: PNG with transparent background

**Footer Image:**
- Width: 2.5 inches (about 200-300 pixels)
- Height: Proportional to width
- Best format: PNG or JPG

### Result

When you generate a document:
1. Logo appears in the header (top left)
2. Company info appears in the footer (bottom right)
3. Both appear on every page automatically

### Supported Image Formats

- PNG (recommended for logo with transparency)
- JPG/JPEG (good for photos)
- GIF

### Troubleshooting

If images don't appear:

1. **Check file names:** Must be exactly:
   - `planisware_logo.png`
   - `footer_info.png`

2. **Check location:** Files must be in the `templates/` folder

3. **Check format:** Must be PNG, JPG, or GIF

4. **Check size:** Very large images may not fit. Keep under 1000x1000 pixels each.

### How to Replace Images on Windows

1. Open File Explorer
2. Navigate to: `New Project/templates/`
3. Delete the placeholder images
4. Copy your actual images into this folder
5. Rename them to match exactly:
   - `planisware_logo.png`
   - `footer_info.png`
6. Restart the application
7. Generate a new document - images should now appear!

---

**Note:** The application includes placeholder images for demonstration. Replace them with your actual company logo and footer for production use.
