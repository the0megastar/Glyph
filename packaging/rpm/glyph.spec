Name:           glyph
Version:        0.1.3
Release:        1%{?dist}
Summary:        Customize application launcher icons and names on Linux
License:        GPL-3.0-or-later
URL:            https://github.com/the0megastar/Glyph
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  meson >= 0.63.0
BuildRequires:  ninja-build
BuildRequires:  python3-devel
BuildRequires:  python3-gobject-devel
BuildRequires:  pkgconfig(gtk4) >= 4.10
BuildRequires:  pkgconfig(libadwaita-1) >= 1.5
BuildRequires:  pkgconfig(pygobject-3.0)
BuildRequires:  desktop-file-utils

Requires:       python3 >= 3.10
Requires:       python3-gobject
Requires:       gtk4 >= 4.10
Requires:       libadwaita >= 1.5
Requires:       desktop-file-utils
Requires:       hicolor-icon-theme

%description
Glyph is a modern desktop utility for Linux that allows users to browse
installed applications, customize launcher icons and display names safely
at the user level, and easily restore system defaults.

%prep
%autosetup

%build
%meson
%meson_build

%install
%meson_install

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/*.desktop
%{__python3} -m unittest discover -s tests -v

%post
/usr/bin/update-desktop-database &> /dev/null || :
/usr/bin/gtk-update-icon-cache %{_datadir}/icons/hicolor &> /dev/null || :

%postun
/usr/bin/update-desktop-database &> /dev/null || :
/usr/bin/gtk-update-icon-cache %{_datadir}/icons/hicolor &> /dev/null || :

%files
%license LICENSE
%doc README.md
%{_bindir}/glyph
%{python3_sitelib}/glyph
%{_datadir}/applications/io.github.the0megastar.Glyph.desktop
%{_datadir}/metainfo/io.github.the0megastar.Glyph.metainfo.xml
%{_datadir}/icons/hicolor/scalable/apps/io.github.the0megastar.Glyph.svg

%changelog
* Sun Sep 06 2026 the0megastar <the0megastar@users.noreply.github.com> - 0.1.3-1
- Version 0.1.3: overrides, transaction journal, bulk revert names, v2 backups, and hardened paths
