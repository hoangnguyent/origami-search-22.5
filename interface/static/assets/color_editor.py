import xml.etree.ElementTree as ET
import colorsys

# Register standard SVG namespaces so they output cleanly without "ns0:" prefixes
ET.register_namespace('', 'http://www.w3.org/2000/svg')
ET.register_namespace('xlink', 'http://www.w3.org/1999/xlink')

def hex_to_rgb(hex_color):
    """Convert a hex color string to an RGB tuple (0.0 to 1.0)."""
    hex_color = hex_color.lstrip('#')
    if len(hex_color) == 3:
        hex_color = ''.join(c + c for c in hex_color)
    return tuple(int(hex_color[i:i+2], 16) / 255.0 for i in (0, 2, 4))

def rgb_to_hex(r, g, b):
    """Convert an RGB tuple (0.0 to 1.0) back to a hex string."""
    return '#{:02x}{:02x}{:02x}'.format(int(r * 255), int(g * 255), int(b * 255))

def adjust_color(hex_color, hue_shift, contrast_factor):
    """Shifts the hue and adjusts the contrast of a hex color."""
    try:
        r, g, b = hex_to_rgb(hex_color)
        h, l, s = colorsys.rgb_to_hls(r, g, b)
        
        # Only shift if it actually has color (skip pure whites, blacks, and grays)
        if s > 0.05:
            # 1. Shift hue and wrap around using modulo 1.0
            h = (h + hue_shift) % 1.0
            
            # 2. Apply contrast to Lightness
            # Push the lightness away from 0.5 (middle gray)
            l = (l - 0.5) * contrast_factor + 0.5
            
            # Clamp the lightness so it doesn't break out of the 0.0 - 1.0 bounds
            l = max(0.0, min(1.0, l))
            
            r, g, b = colorsys.hls_to_rgb(h, l, s)
            return rgb_to_hex(r, g, b)
    except ValueError:
        pass # Return original if parsing fails
    
    return hex_color

def process_animated_svg(input_path, output_path, hue_shift_amount=-0.35, contrast_factor=1.25):
    tree = ET.parse(input_path)
    root = tree.getroot()

    # 1. Remove the lowest z-height background circle
    removed_bg = False
    for parent in root.iter():
        if removed_bg:
            break
        for child in list(parent):
            tag_name = child.tag.split('}')[-1]
            if tag_name == 'path':
                fill_color = child.attrib.get('fill', '').lower()
                # Remove the first massive path that acts as the white/off-white background
                if fill_color in ['#f5f8fa', '#ffffff']:
                    parent.remove(child)
                    removed_bg = True
                    break

    # 2. Hue-shift and contrast-adjust all the colors
    color_attributes = ['fill', 'stroke', 'stop-color']
    
    for elem in root.iter():
        for attr in color_attributes:
            if attr in elem.attrib:
                val = elem.attrib[attr]
                # If the attribute is a hex color, apply the shift and contrast
                if val.startswith('#'):
                    new_color = adjust_color(val, hue_shift_amount, contrast_factor)
                    elem.attrib[attr] = new_color

    # Save the modified tree
    tree.write(output_path, encoding='utf-8', xml_declaration=True)
    print(f"Successfully processed SVG and saved to '{output_path}'")

if __name__ == "__main__":
    # --- Configuration ---
    input_file = "/Users/bwong/Desktop/SEARCH-22.5/interface/static/assets/robot_loading.svg"
    output_file = "/Users/bwong/Desktop/SEARCH-22.5/interface/static/assets/robot_loading_new.svg"
    
    # Hue shift: -0.33 shifts blues squarely into the greens.
    shift_amount = 0.05 
    
    # Contrast factor: 
    # 1.0 = no change
    # 1.25 = 25% more contrast (lights get lighter, darks get darker)
    # 1.5 = heavily contrasted
    contrast = 0.3
    
    process_animated_svg(input_file, output_file, hue_shift_amount=shift_amount, contrast_factor=contrast)