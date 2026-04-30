# MakeLive
<!-- ALL-CONTRIBUTORS-BADGE:START - Do not remove or modify this section -->
[![All Contributors](https://img.shields.io/badge/all_contributors-2-orange.svg?style=flat-square)](#contributors-)
<!-- ALL-CONTRIBUTORS-BADGE:END -->

Convert an photo + video pair into a Live Photo.

This is a simple command line tool that will apply the necessary metadata to a photo + video pair so that when they are imported into the Apple Photos, they will be treated as a Live Photo.

This is useful for converting images taken an Android phone into Live Photos that can be imported into Apple Photos.

## Usage

```bash
makelive image_1234.jpg image_1234.mov
```

## Requirements

- macOS (Tested on 13.5.1; should work on 10.15+)
- Python 3.9+

## Installation

### Install via Pre-Built Binary Installer Package

Download and run the latest installer package for your Mac architecture from the [releases page](https://github.com/RhetTbull/makelive/releases).

### Install via uv

Alternatively, you can install with uv:

- Install [uv](https://docs.astral.sh/uv/getting-started/installation/):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

- Install makelive:

```bash
uv install makelive
```

Alternatively, you can run makelive with uv without installing it:

```bash
uvx makelive image_1234.jpg image_1234.mov
```

**Note**: This package may not install with `pip` due to a dependency resolution issue. PRs are welcome.

### Install from Source

To install from source:

- Install [uv](https://docs.astral.sh/uv/getting-started/installation/): `curl -LsSf https://astral.sh/uv/install.sh | sh`
- `git clone git@github.com:RhetTbull/makelive.git`
- `cd makelive`
- `uv venv`
- `source .venv/bin/activate`
- `uv pip install flit`
- `flit install`

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

Live Photos can also be created as a [.pvt package](https://fileinfo.com/extension/pvt). Use `save_live_photo_pair_as_pvt` to create a .pvt package from a photo and video pair. This is useful for creating Live Photos that can be shared via AirDrop or other methods that may not preserve the Live Photo metadata. Unlike `make_live_photo`, `save_live_photo_pair_as_pvt` does not modify the original photo and video files but instead copies them into a `.pvt` package and modifies the copies. If the original photo and video are already a Live Photo pair, the `.pvt` package will be created with the same asset ID; if not, a new asset ID will be generated.

```python
from makelive import save_live_photo_pair_as_pvt
photo_path = "test.jpg"
video_path = "test.mov"
asset_id, pvt_path = save_live_photo_pair_as_pvt(photo_path, video_path)
print(f"Wrote .pvt package to {pvt_path} with {asset_id}")
```

> [!NOTE]
> XMP metadata in the QuickTime movie file is not preserved when writing the Content Identifier tag to the movie file which may result in metadata loss.

Metadata including EXIF, IPTC, and XMP are preserved in the image file but will be rewritten and the Core Graphics API may change the order of the metadata and normalize the values. For example, the tag XMP:TagsList will be rewritten as XMP:Subject and the value will be normalized to a list of title case strings.

If you must preserve the original metadata completely, it is recommended to make a copy of the metadata using a tool like [exiftool](https://exiftool.org) before calling this function and then restore the metadata after calling this function. (But take care not to delete the `ContentIdentifier` metadata.)

## How it works

In order for Photos to treat a photo + video pair as a Live Photo, the video file must contain a Content Identifier metadata tag set to a [UUID](https://en.wikipedia.org/wiki/Universally_unique_identifier). The associated photo must contain a Content Identifier metadata tag set to the same UUID. Unfortunately, these tags cannot be written with the standard [exiftool](https://exiftool.org/) utility if they do not already exist in the file as the metadata is stored in Maker Notes which exiftool cannot create.

This tool uses the Core Graphics and AV Foundation frameworks to modify the metadata of the photo and video files to add the required Content Identifier.

## Limitations

The Live Photos created by this tool may not work as Live Wallpapers. I don't user Live Wallpapers and don't have time to debug this. I believe the issue has to do with video format and/or video length. I'm happy to accept a PR but please don't open an issue if Live Wallpapers don't work.

## Caution

> [!WARNING]
> This tool has not yet been extensively tested. It is recommended that you make a backup of your photo and video files before using this tool as it will overwrite the files which is required to add the necessary metadata. This also means that the files will be re-encoded and as a result, the file size may change, as may the quality of the image and video. I've used the native Apple APIs to do the encoding at maxixum quality but you should verify that the results are suitable for your needs.

## Source Code

The source code is available [here](https://github.com/RhetTbull/makelive).

## License

MIT License, see [LICENSE](LICENSE) for details.

## Credits

The [Live-Photo-master](https://github.com/GUIYIVIEW/LivePhoto-master) project by [GUIYIVIEW](https://github.com/GUIYIVIEW) was helpful for understanding how to set the asset ID in the QuickTime file. Copyright (c) 2017 GUIYIVIEW and [published under the MIT License](https://github.com/GUIYIVIEW/LivePhoto-master/blob/master/LICENSE).

Thank you to [Yorian](https://github.com/Yorian) who proposed this project and provided the test images. For more information, see [this discussion](https://github.com/RhetTbull/makelive/discussions/1398).

## Contributors ✨

Thanks goes to these wonderful people ([emoji key](https://allcontributors.org/docs/en/emoji-key)):

<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->
<!-- prettier-ignore-start -->
<!-- markdownlint-disable -->
<table>
  <tbody>
    <tr>
      <td align="center" valign="top" width="14.28%"><a href="http://am1006.me"><img src="https://avatars.githubusercontent.com/u/13403435?v=4?s=100" width="100px;" alt="Luitbald"/><br /><sub><b>Luitbald</b></sub></a><br /><a href="https://github.com/RhetTbull/makelive/commits?author=am1006" title="Code">💻</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/Jaqobs"><img src="https://avatars.githubusercontent.com/u/39723475?v=4?s=100" width="100px;" alt="Jaqobs"/><br /><sub><b>Jaqobs</b></sub></a><br /><a href="https://github.com/RhetTbull/makelive/commits?author=Jaqobs" title="Code">💻</a></td>
    </tr>
  </tbody>
</table>

<!-- markdownlint-restore -->
<!-- prettier-ignore-end -->

<!-- ALL-CONTRIBUTORS-LIST:END -->

This project follows the [all-contributors](https://github.com/all-contributors/all-contributors) specification. Contributions of any kind welcome!
