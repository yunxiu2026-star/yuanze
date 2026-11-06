"""Original vector placeholders; no customer photography or licensed logos."""
from common import ROOT
from html import escape
import json

def generate():
 out=ROOT/'site/static'
 for key,color,shape in [('sage','#91a58a','bottle'),('glass','#b7c8bd','bottle'),('sand','#c8b494','bottle'),('jar','#9eaa93','jar')]:
  product=(f'<rect x="325" y="280" width="250" height="285" rx="35" fill="{color}"/><rect x="320" y="255" width="260" height="65" rx="18" fill="#e6e1d5"/>' if shape=='jar' else f'<rect x="345" y="210" width="210" height="370" rx="55" fill="{color}"/><rect x="395" y="155" width="110" height="75" rx="12" fill="#d7cfbd"/><path d="M450 160v-45h140" fill="none" stroke="#d7cfbd" stroke-width="24" stroke-linejoin="round"/>')
  svg=f'''<svg xmlns="http://www.w3.org/2000/svg" width="900" height="760" viewBox="0 0 900 760"><defs><linearGradient id="bg" x2="1" y2="1"><stop stop-color="#efeee5"/><stop offset="1" stop-color="#d8daca"/></linearGradient><filter id="shadow"><feGaussianBlur stdDeviation="16"/></filter></defs><rect width="900" height="760" fill="url(#bg)"/><circle cx="750" cy="100" r="270" fill="#fff" opacity=".35"/><path d="M0 610L900 500v260H0" fill="#e6dfce"/><ellipse cx="450" cy="590" rx="165" ry="28" fill="#455b49" opacity=".22" filter="url(#shadow)"/>{product}<rect x="365" y="335" width="170" height="120" rx="4" fill="#f8f5eb" opacity=".88"/><text x="450" y="380" text-anchor="middle" font-family="sans-serif" font-size="21" fill="#173f34" letter-spacing="3">YOUR BRAND</text><text x="450" y="414" text-anchor="middle" font-family="sans-serif" font-size="13" fill="#506554" letter-spacing="2">PRODUCT CONCEPT</text><text x="42" y="714" font-family="sans-serif" font-size="14" fill="#66796c" letter-spacing="3">ILLUSTRATIVE PACKAGING · REPLACE WITH YOUR PRODUCT</text></svg>'''
  (out/f'product-{key}.svg').write_text(svg,encoding='utf-8')
 for name in ['brand-logo.svg','brand-icon.svg']:
  (out/name).write_text('<svg xmlns="http://www.w3.org/2000/svg" width="120" height="90" viewBox="0 0 120 90"><path d="M30 58V22h28c27 0 27 19 9 20 24 0 24 23-4 23H30" fill="none" stroke="#173f34" stroke-width="6"/><text x="60" y="85" text-anchor="middle" font-family="sans-serif" font-size="12" letter-spacing="4" fill="#173f34">B2B</text></svg>',encoding='utf-8')
 print('Original example SVG assets generated.')
if __name__=='__main__':generate()
