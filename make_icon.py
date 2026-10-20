"""Generate the app's small vector-style book and playback icon."""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
assets = ROOT/'assets'
assets.mkdir(exist_ok=True)
image = Image.new('RGBA', (1024,1024))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((24,24,1000,1000), radius=218, fill='#153759')
draw.rounded_rectangle((218,154,808,864), radius=55, fill='#f6f9fc')
draw.rounded_rectangle((171,185,255,832), radius=24, fill='#cbdcec')
draw.rounded_rectangle((324,262,680,292), radius=15, fill='#a6bfd6')
draw.rounded_rectangle((324,340,628,370), radius=15, fill='#a6bfd6')
draw.rounded_rectangle((324,714,584,744), radius=15, fill='#a6bfd6')
draw.ellipse((374,416,870,912), fill='#32c7b0')
draw.polygon([(573,528),(573,799),(773,664)], fill='#ffffff')
image.save(assets/'app.png')
image.save(assets/'app.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
