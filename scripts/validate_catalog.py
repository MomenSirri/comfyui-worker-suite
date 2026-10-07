"""Verify supported build plans and in-repository contexts without building."""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    catalog = json.loads((ROOT / 'build-catalog.json').read_text(encoding='utf-8'))
    names = [item['name'] for item in catalog['targets']]
    if len(names) != 24 or len(names) != len(set(names)):
        raise SystemExit('Expected 24 unique supported build targets')
    result = subprocess.run(
        ['docker', 'buildx', 'bake', '-f', 'docker-bake.hcl', *names, '--print'],
        cwd=ROOT, text=True, capture_output=True, check=True,
    )
    plan = json.loads(result.stdout)
    if set(plan['target']) != set(names):
        raise SystemExit('Catalog and Bake targets differ')
    for name, target in plan['target'].items():
        if 'dfr' in name.lower() or target['dockerfile'] not in ('Dockerfile', 'Dockerfile.4k', 'Dockerfile.cpu'):
            raise SystemExit(f'Unsupported standalone recipe: {name}')
        context = (ROOT / target['context']).resolve()
        if not context.is_relative_to(ROOT) or not (context / target['dockerfile']).is_file():
            raise SystemExit(f'Missing or external build context: {name}')
        if target.get('platforms') != ['linux/amd64']:
            raise SystemExit(f'Wrong deployment platform: {name}')
    for name, profile in [('ltx25-int8', 'int8'), ('ltx25-bf16', 'bf16'), ('ltx25-cq-v2', 'cq-v2')]:
        if plan['target'][name]['args']['MODEL_PROFILE'] != profile:
            raise SystemExit(f'Wrong model profile: {name}')
    if plan['target']['ltx25-4k']['contexts']['ltx25-model-base'] != 'target:ltx25-int8':
        raise SystemExit('ComfyUI 4K INT8 dependency is missing')
    default_result = subprocess.run(
        ['docker', 'buildx', 'bake', '-f', 'docker-bake.hcl', '--print'],
        cwd=ROOT, text=True, capture_output=True, check=True,
    )
    if set(json.loads(default_result.stdout)['target']) != {'generic-comfyui'}:
        raise SystemExit('Default build must select only the generic worker')
    print('PASS: 24 ComfyUI build plans, local contexts, profiles, 4K dependency, and generic-only default')


if __name__ == '__main__':
    main()
