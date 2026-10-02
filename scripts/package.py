"""Build a source-only delivery ZIP from explicit paths, without runtime data."""
from pathlib import Path
import hashlib
import zipfile

ROOT = Path(__file__).resolve().parent.parent
TOP = ('README.md', 'PROVENANCE.md', 'VALIDATION.md', 'requirements.lock', 'mime.types',
       'Dockerfile', 'compose.yaml', '.env.example', '.discord.env.example', '.gitignore', '.dockerignore')
TREES = ('source', 'scripts', 'deploy', 'tests')


def files():
    result = [ROOT / name for name in TOP]
    for name in TREES:
        result.extend(p for p in (ROOT / name).rglob('*') if p.is_file()
                      and '__pycache__' not in p.parts and p.suffix != '.pyc')
    return sorted(result)


def main():
    output = ROOT / 'dist' / 'game-alert-macos-discord-1.0.0.zip'
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files():
            if path.is_symlink():
                raise ValueError('Symlinks are not distribution sources')
            archive.write(path, 'game-alert/' + str(path.relative_to(ROOT)))
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix('.zip.sha256').write_text(digest + '  ' + output.name + '\n')
    print(output)
    print('sha256:', digest)


if __name__ == '__main__':
    main()
