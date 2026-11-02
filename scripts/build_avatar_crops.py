"""Build self-contained SVG clipping views; original WebP pixels stay unchanged."""
import base64
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]/'backend/app/static/images/avatars'
CROP_BOTTOM={**{f'base-01-{n:02}':4 if n<=4 else 12 if n<=8 else 16 for n in range(1,17)},
             'base-03-02':12,'base-03-03':12}


def main():
    catalog_path=ROOT/'catalog.json'
    catalog=json.loads(catalog_path.read_text(encoding='utf-8'))
    for avatar in catalog['avatars']:
        key=avatar['key']
        if key not in CROP_BOTTOM:
            continue
        # The old sheet crop drifts farther into the next row down the sheet.
        bottom=CROP_BOTTOM[key]
        avatar['display_crop']={'reference_size':160,'bottom':bottom}
        avatar['display_images']={}
        for size in (64,160):
            original=(ROOT/avatar['images'][str(size)]['file']).read_bytes()
            side=size*(160-bottom)/160
            left=(size-side)/2
            svg=(f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
                 f'viewBox="{left:g} 0 {side:g} {side:g}">'
                 f'<image width="{size}" height="{size}" href="data:image/webp;base64,'
                 +base64.b64encode(original).decode('ascii')+'"/></svg>\n').encode('utf-8')
            filename=f'{key}-{size}-crop.svg'
            (ROOT/filename).write_bytes(svg)
            avatar['display_images'][str(size)]={'file':filename,'bytes':len(svg),'sha256':hashlib.sha256(svg).hexdigest()}
    catalog_path.write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

if __name__=='__main__':main()
