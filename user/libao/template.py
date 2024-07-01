pkgname = "libao"
pkgver = "1.2.0"
pkgrel = 0
build_style = "configure"
pkgdesc = "Cross-platform audio output library"
maintainer = "William Kent <wjk011@gmail.com>"
license = "GPL-2.0-or-later"
url = "https://www.xiph.org/ao/index.html"
source = "https://downloads.xiph.org/releases/ao/libao-1.2.0.tar.gz"
sha256 = "03ad231ad1f9d64b52474392d63c31197b0bc7bd416e58b1c10a329a5ed89caf"

hostmakedepends = [
    'pkgconf'
]

configure_args = [
    '--prefix=/usr'
]

@subpackage(f"{pkgname}-devel")
def _devel(self):
    return self.default_devel()
