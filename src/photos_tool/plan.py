"""Build the ``osxphotos export`` command line from high-level options.

This is pure logic with no I/O, so it can be exercised completely in unit tests —
the part that actually runs the command needs a real Photos library and is tested
by hand on a Mac (see README).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExportOptions:
    """Everything needed to assemble one export invocation."""

    destination: str
    scope: str = "selected"  # "selected" (the live Photos selection) or "album"
    album: str | None = None
    update: bool = True
    exportdb: str | None = None
    exiftool: bool = True
    download_missing: bool = True
    use_photokit: bool = False
    touch_file: bool = True
    retry: int = 3
    convert_to_jpeg: bool = False
    jpeg_quality: float = 0.9
    jpeg_ext: str = "jpeg"
    only_photos: bool = False
    skip_live: bool = False
    directory_template: str = "{created.year}/{created.mm}"
    filename_template: str = "{original_name}"


def build_export_command(opts: ExportOptions) -> list[str]:
    """Translate :class:`ExportOptions` into an ``osxphotos`` argv list."""
    if not opts.destination:
        raise ValueError("destination is required")
    if opts.scope != "selected" and not opts.album:
        raise ValueError("specify the live selection (scope='selected') or an album name")

    cmd = ["osxphotos", "export", opts.destination]
    if opts.scope == "selected":
        cmd.append("--selected")
    if opts.album:
        cmd += ["--album", opts.album]
    if opts.update:
        cmd.append("--update")
    if opts.exportdb:
        cmd += ["--exportdb", opts.exportdb]
    if opts.exiftool:
        cmd += ["--exiftool", "--exiftool-merge-keywords", "--exiftool-merge-persons"]
    if opts.download_missing:
        cmd.append("--download-missing")
    if opts.use_photokit:
        cmd.append("--use-photokit")
    if opts.touch_file:
        cmd.append("--touch-file")
    if opts.retry > 0:
        cmd += ["--retry", str(opts.retry)]
    if opts.convert_to_jpeg:
        cmd += ["--convert-to-jpeg", "--jpeg-quality", f"{opts.jpeg_quality:g}"]
        if opts.jpeg_ext:
            cmd += ["--jpeg-ext", opts.jpeg_ext]
    if opts.only_photos:
        cmd.append("--only-photos")
    if opts.skip_live:
        cmd.append("--skip-live")
    cmd += ["--directory", opts.directory_template, "--filename", opts.filename_template]
    return cmd
