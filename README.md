# MakeLive

Convert an photo + video pair into a Live Photo.

This is a simple command line tool that will apply the necessary metadata to a photo + video pair so that when they are imported into the Apple Photos, they will be treated as a Live Photo.

This is useful for converting images taken an Android phone into Live Photos that can be imported into Apple Photos.

## Usage

```bash
makelive image_1234.jpg image_1234.mov
```

## Requirements

* macOS (Tested on 13.5.1; should work on 10.15+)
* Python 3.9+

## Installation

### Install via Pre-Built Binary Installer Package

Download and run the latest installer package for your Mac architecture from the [releases page](https://github.com/RhetTbull/makelive/releases).

### Install via pip or pipx

Alternatively, you can install from PyPI:

* `python3 -m pip install makelive`

Or you can use [pipx](https://github.com/pypa/pipx):

* Open `Terminal` (search for `Terminal` in Spotlight or look in `Applications/Utilities`)
* Install `homebrew` according to instructions at [https://brew.sh/](https://brew.sh/)
* Type the following into Terminal: `brew install pipx`
* Ensure that pipx installed packages are accessible in your PATH by typing: `pipx ensurepath`
* Then type this: `pipx install makelive`
* Now you should be able to run `makelive` by typing: `makelive`

Once you've installed makelive with pipx, to upgrade to the latest version:

    pipx upgrade makelive

[!NOTE]
When installing other packages with homebrew, homebrew may update the version of Python installed which would then cause any app (including makelive) installed with `pipx` to fail. If this happens, the easiest fix is to reinstall makelive with:

    pipx reinstall makelive

Alternatively, you can reinstall all apps installed with `pipx` with:

    pipx reinstall-all

### Install from Source

To install from source:

* `git clone git@github.com:RhetTbull/makelive.git`
* `cd makelive`
* `pip install flit`
* `flit install`

## API

You can use makelive to programmatically create Live Photo pairs:

```python
from makelive import make_live_photo

photo_path = "test.jpg"
video_path = "test.mov"
asset_id = make_live_photo(photo_path, video_path)
print(f"Wrote Asset ID: {asset_id} to {photo_path} and {video_path}")
```

You can also check if a photo and video pair are a Live Photo pair and get the asset ID:

```python
from makelive import live_id, is_live_photo_pair
photo_path = "test.jpg"
video_path = "test.mov"
print(f"Is Live Photo Pair: {is_live_photo_pair(photo_path, video_path)}")
print(f"Asset ID: {live_id(photo_path)}")
```

[!NOTE]
XMP metadata in the QuickTime movie file is not preserved by this function which may result in metadata loss.

Metadata including EXIF, IPTC, and XMP are preserved in the image file but will be rewritten and the Core Graphics API may change the order of the metadata and normalize the values. For example, the tag XMP:TagsList will be rewritten as XMP:Subject and the value will be normalized to a list of title case strings.

If you must preserve the original metadata completely, it is recommended to make a copy of the metadata using a tool like [exiftool](https://exiftool.org) before calling this function and then restore the metadata after calling this function. (But take care not to delete the ContentIdentifier metadata.)

## How it works

In order for Photos to treat a photo + video pair as a Live Photo, the video file must contain a Content Identifier metadata tag set to a UUID. The associated photo must contain a Content Identifier metadata tag set to the same UUID. Unfortunately, these tags cannot be written with the standard [exiftool](https://exiftool.org/) utility if they do not already exist in the file as the metadata is stored in Maker Notes which exiftool cannot create.

This tool uses the Core Graphics and AV Foundation frameworks to modify the metadata of the photo and video files.

## Caution

[!WARNING]
This tool has not yet been extensively tested. It is recommended that you make a backup of your photo and video files before using this tool as it will overwrite the files which is required to add the necessary metadata. This also means that the files will be re-encoded and as a result, the file size may change, as may the quality of the image and video. I've used the native Apple APIs to do the encoding at maxixum quality but you should verify that the results are suitable for your needs.

## Source Code

The source code is available [here](https://github.com/RhetTbull/makelive).

## License

MIT License, see [LICENSE](LICENSE) for details.

## Credits

The [Live-Photo-master](https://github.com/GUIYIVIEW/LivePhoto-master) project by [GUIYIVIEW](https://github.com/GUIYIVIEW) was helpful for understanding how to set the asset ID in the QuickTime file. Copyright (c) 2017 GUIYIVIEW and [published under the MIT License](https://github.com/GUIYIVIEW/LivePhoto-master/blob/master/LICENSE).

Thank you to [Yorian](https://github.com/Yorian) who proposed this project and provided the test images. For more information, see [this discussion](https://github.com/RhetTbull/makelive/discussions/1398).
