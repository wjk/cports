import subprocess
import os
import re
import glob
import shutil
import shlex
import getpass
import pathlib
import binascii
from tempfile import mkstemp

from cbuild.core import logger, paths, errors
from cbuild.apk import cli as apki

_chroot_checked = False
_chroot_ready = False

def host_cpu():
    return _host

def target_cpu():
    return _target

def set_target(tgt):
    global _target
    _target = tgt

def set_host(tgt):
    global _host
    _host = tgt

def chroot_check(force = False):
    global _chroot_checked, _chroot_ready

    if _chroot_checked and not force:
        return _chroot_ready

    _chroot_checked = True

    if (paths.bldroot() / ".cbuild_chroot_init").is_file():
        _chroot_ready = True
        cpun = (paths.bldroot() / ".cbuild_chroot_init").read_text().strip()
    else:
        _chroot_ready = False
        cpun = apki.get_arch()

    set_host(cpun)
    set_target(cpun)

    return _chroot_ready

def _subst_in(pat, rep, src, dest = None):
    inf = open(src, "r")
    if dest:
        outf = open(dest, "w")
    else:
        fd, nm = mkstemp()
        outf = open(nm, "w")

    for line in inf:
        out = re.sub(pat, rep, line)
        outf.write(out)

    inf.close()
    outf.close()

    if not dest:
        shutil.move(nm, src)

def _remove_ro(f, path, _):
    os.chmod(path, stat.S_IWRITE)
    f(path)

def _prepare_passwd():
    bfp = paths.distdir() / "main/base-files/files"
    tfp = paths.bldroot() / "etc"

    shutil.copy(bfp / "etc/passwd", tfp)
    shutil.copy(bfp / "etc/group", tfp)

    with open(tfp / "passwd", "a") as pf:
        pf.write(f"cbuild:x:1337:1337:cbuild user:/tmp:/bin/nologin\n")

    with open(tfp / "group", "a") as pf:
        pf.write(f"cbuild:x:1337:\n")

def _init():
    xdir = paths.bldroot() / "etc" / "apk"
    xdir.mkdir(parents = True, exist_ok = True)

    shutil.copy("/etc/resolv.conf", paths.bldroot() / "etc")
    # generate machine-id
    with open(paths.bldroot() / "etc/machine-id", "wb") as mid:
        mid.write(b"%s\n" % binascii.b2a_hex(os.urandom(16)))

def _prepare(arch, stage):
    sfpath = paths.bldroot() / ".cbuild_chroot_init"
    if sfpath.is_file():
        return
    if not (paths.bldroot() / "usr" / "bin" / "sh").is_file():
        raise errors.CbuildException("bootstrap not installed, can't continue")

    (paths.bldroot() / "etc" / "localtime").symlink_to(
        "../usr/share/zoneinfo/UTC"
    )

    if (paths.bldroot() / "usr/bin/update-ca-certificates").is_file():
        enter("update-ca-certificates", "--fresh")

    _prepare_passwd()

    with open(sfpath, "w") as sf:
        sf.write(arch + "\n")

def setup_keys(rootp):
    # copy over apk public keys
    keydir = rootp / "etc/apk/keys"

    shutil.rmtree(keydir, ignore_errors = True)
    keydir.mkdir(parents = True, exist_ok = True)

    for f in (paths.distdir() / "etc/apk/keys").glob("*.pub"):
        shutil.copy2(f, keydir)

    for f in (paths.distdir() / "etc/keys").glob("*.pub"):
        shutil.copy2(f, keydir)

_crepos = None

def get_confrepos():
    global _crepos

    if _crepos:
        return _crepos

    _crepos = []
    for f in (paths.distdir() / "etc/apk/repositories.d").glob("*.conf"):
        with open(f) as repof:
            for repo in repof:
                _crepos.append(repo.strip())

    return _crepos

def repo_sync(genrepos = False, rnet = True):
    setup_keys(paths.bldroot())

    # generate a repositories file for chroots
    rfile = paths.bldroot() / "etc/apk/repositories"
    # erase first in any case
    rfile.unlink(missing_ok = True)
    # generate only if needed (for explicit chroots)
    if genrepos:
        with rfile.open("w") as rfh:
            for rd in paths.repository().iterdir():
                for cr in get_confrepos():
                    if not cr.startswith("/"):
                        continue
                    cr = cr.lstrip("/")
                    idxp = rd / cr / host_cpu() / "APKINDEX.tar.gz"
                    if idxp.is_file():
                        rfh.write(f"/binpkgs/{rd.name}/{cr}\n")
            if paths.alt_repository():
                for rd in paths.alt_repository().iterdir():
                    for cr in get_confrepos():
                        if not cr.startswith("/"):
                            continue
                        cr = cr.lstrip("/")
                        idxp = rd / cr / host_cpu() / "APKINDEX.tar.gz"
                        if idxp.is_file():
                            rfh.write(f"/altbinpkgs/{rd.name}/{cr}\n")
            # remote repos come last
            if rnet:
                for rd in paths.repository().iterdir():
                    for cr in get_confrepos():
                        if cr.startswith("/"):
                            continue
                        rfh.write(cr.replace("@section@", rd.name))
                        rfh.write("\n")

    # do not refresh if chroot is not initialized
    if not (paths.bldroot() / ".cbuild_chroot_init").is_file():
        return

    chflags = []
    if not genrepos:
        chflags = ["-q"]

    if apki.call_chroot(
        "update", chflags, "main", full_chroot = genrepos, allow_network = rnet
    ).returncode != 0:
        raise errors.CbuildException(f"failed to update pkg database")

def initdb(path = None):
    # we init the database ourselves
    if not path:
        path = paths.bldroot()

    (path / "tmp").mkdir(parents = True, exist_ok = True)
    (path / "dev").mkdir(parents = True, exist_ok = True)
    (path / "etc/apk").mkdir(parents = True, exist_ok = True)
    (path / "usr/lib/apk/db").mkdir(parents = True, exist_ok = True)
    (path / "var/cache/apk").mkdir(parents = True, exist_ok = True)
    (path / "var/cache/misc").mkdir(parents = True, exist_ok = True)
    (path / "var/log").mkdir(parents = True, exist_ok = True)

    # largely because of custom usrmerge
    if not (path / "lib").is_symlink():
        (path / "lib").symlink_to("usr/lib")

    (path / "usr/lib/apk/db/installed").touch()
    (path / "etc/apk/world").touch()

def install(arch = None, stage = 2):
    if chroot_check():
        return

    logger.get().out("cbuild: installing base-cbuild...")

    initdb()

    if not arch or stage < 2:
        arch = host_cpu()

    set_host(arch)
    set_target(arch)
    repo_sync()

    irun = apki.call(
        "add", ["--no-scripts", "base-cbuild"], "main", arch = arch,
        fakeroot = True
    )
    if irun.returncode != 0:
        raise errors.CbuildException("failed to install base-cbuild")

    logger.get().out("cbuild: installed base-cbuild successfully!")

    paths.prepare()
    _prepare(arch, stage)
    _chroot_checked = False
    _chroot_ready = False
    chroot_check()
    _init()

def get_fakeroot(bootstrap):
    inp = paths.cbuild() / "misc/fakeroot.sh"

    if bootstrap:
        return inp

    rp = paths.bldroot() / ".cbuild_fakeroot.sh"

    if rp.is_file():
        return "/.cbuild_fakeroot.sh"

    rp.unlink(missing_ok = True)
    shutil.copyfile(inp, rp)

    return "/.cbuild_fakeroot.sh"

def remove_autodeps(bootstrapping):
    if bootstrapping is None:
        bootstrapping = not (paths.bldroot() / ".cbuild_chroot_init").is_file()

    log = logger.get()

    log.out("cbuild: removing autodeps...")

    failed = False

    paths.prepare()

    if apki.call("info", [
        "--installed", "autodeps-host"
    ], None, capture_output = True, allow_untrusted = True).returncode == 0:
        if bootstrapping:
            del_ret = apki.call("del", [
                "--no-scripts", "autodeps-host"
            ], None, capture_output = True, fakeroot = True)
        else:
            del_ret = apki.call_chroot(
                "del", ["autodeps-host"], None, capture_output = True
            )

        if del_ret.returncode != 0:
            log.out_plain(">> stderr (host):")
            log.out_plain(del_ret.stderr.decode())
            failed = True

    if apki.call("info", [
        "--installed", "autodeps-target"
    ], None, capture_output = True, allow_untrusted = True).returncode == 0:
        if bootstrapping:
            del_ret = apki.call("del", [
                "--no-scripts", "autodeps-target"
            ], None, capture_output = True, fakeroot = True)
        else:
            del_ret = apki.call_chroot(
                "del", ["autodeps-target"], None, capture_output = True
            )

        if del_ret.returncode != 0:
            log.out_plain(">> stderr (target):")
            log.out_plain(del_ret.stderr.decode())
            failed = True

    if failed:
        raise errors.CbuildException("failed to remove autodeps")

def update():
    if not chroot_check():
        return

    logger.get().out("cbuild: updating software in %s container..." \
        % str(paths.bldroot()))

    paths.prepare()

    # reinit passwd/group
    _prepare_passwd()

    apki.call_chroot("update", ["-q"], "main", check = True, use_stage = False)
    apki.call_chroot(
        "upgrade", ["--available"], "main", check = True, use_stage = False
    )

def enter(cmd, *args, capture_output = False, check = False,
          env = {}, stdout = None, stderr = None, wrkdir = None,
          bootstrapping = False, ro_root = False, ro_build = False,
          ro_dest = True, unshare_all = False, mount_binpkgs = False,
          mount_cbuild_cache = False, mount_cports = False,
          fakeroot = False, new_session = True, binpkgs_rw = False,
          signkey = None, wrapper = None):
    defpath = "/usr/bin"
    if bootstrapping:
        defpath = os.environ["PATH"]

    envs = {
        "PATH": defpath,
        "SHELL": "/bin/sh",
        "HOME": "/tmp",
        "LC_COLLATE": "C",
        "LANG": "C.UTF-8",
        "UNAME_m": host_cpu(),
        **env
    }

    if not unshare_all:
        if "NO_PROXY" in os.environ:
            envs["NO_PROXY"] = os.environ["NO_PROXY"]
        if "FTP_PROXY" in os.environ:
            envs["FTP_PROXY"] = os.environ["FTP_PROXY"]
        if "HTTP_PROXY" in os.environ:
            envs["HTTP_PROXY"] = os.environ["HTTP_PROXY"]
        if "HTTPS_PROXY" in os.environ:
            envs["HTTPS_PROXY"] = os.environ["HTTPS_PROXY"]
        if "SOCKS_PROXY" in os.environ:
            envs["SOCKS_PROXY"] = os.environ["SOCKS_PROXY"]
        if "FTP_RETRIES" in os.environ:
            envs["FTP_RETRIES"] = os.environ["FTP_RETRIES"]
        if "HTTP_PROXY_AUTH" in os.environ:
            envs["HTTP_PROXY_AUTH"] = os.environ["HTTP_PROXY_AUTH"]

    # if running from template, ensure wrappers are early in executable path
    if "CBUILD_STATEDIR" in envs:
        envs["PATH"] = envs["CBUILD_STATEDIR"] + "/wrappers:" + envs["PATH"]

    if new_session:
        envs["PYTHONUNBUFFERED"] = "1"

    # ccache path is searched first
    #
    # this has the implication of having ccache invoke whatever cc wrapper
    # we have at the time, rather than the other way around, which means
    # the wrappers don't have to account for ccache explicitly
    if "CCACHEPATH" in envs:
        envs["PATH"] = envs["CCACHEPATH"] + ":" + envs["PATH"]

    if ro_root:
        root_bind = "--ro-bind"
    else:
        root_bind = "--bind"

    if ro_build:
        build_bind = "--ro-bind"
    else:
        build_bind = "--bind"

    if ro_dest:
        dest_bind = "--ro-bind"
    else:
        dest_bind = "--bind"

    if bootstrapping:
        bcmd = []
        if fakeroot:
            envs["FAKEROOTDONTTRYCHOWN"] = "1"
            bcmd = ["sh", get_fakeroot(True)]
        return subprocess.run(
            [*bcmd, cmd, *args], env = envs,
            capture_output = capture_output, check = check,
            stdout = stdout, stderr = stderr,
            cwd = os.path.abspath(wrkdir) if wrkdir else None
        )

    bcmd = [
        "bwrap",
        "--unshare-all",
        "--hostname", "cbuild",
        root_bind, paths.bldroot(), "/",
        build_bind, paths.bldroot() / "builddir", "/builddir",
        dest_bind, paths.bldroot() / "destdir", "/destdir",
        "--ro-bind", paths.sources(), "/sources",
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",
        "--tmpfs", "/var/tmp",
    ]

    if new_session:
        bcmd += ["--new-session", "--die-with-parent"]

    if mount_binpkgs:
        bcmd += [
            "--ro-bind" if not binpkgs_rw else "--bind", paths.repository(),
            "/binpkgs"
        ]
        if paths.alt_repository():
            bcmd += ["--ro-bind", paths.alt_repository(), "/altbinpkgs"]
        srepo = paths.stage_repository()
        if srepo:
            bcmd += [
                "--ro-bind" if not binpkgs_rw else "--bind",
                srepo, "/stagepkgs"
            ]

    if mount_cbuild_cache:
        bcmd += ["--bind", paths.cbuild_cache(), "/cbuild_cache"]

    # always bubblewrap as cbuild user
    # root-needing things are done through fakeroot so we can chown
    bcmd += ["--uid", "1337"]
    bcmd += ["--gid", "1337"]

    if not unshare_all:
        bcmd += ["--share-net"]

    if wrkdir:
        bcmd.append("--chdir")
        bcmd.append(wrkdir)

    # extra file descriptors to pass to sandbox and bind to a file
    fdlist = []

    if signkey:
        # reopen as file descriptor to pass
        signfd = os.open(signkey, os.O_RDONLY)
        fdlist.append(signfd)
        bcmd += ["--ro-bind-data", str(signfd), f"/tmp/{signkey.name}"]

    if wrapper:
        rfd, wfd = os.pipe()
        os.write(wfd, wrapper.encode())
        os.close(wfd)
        fdlist.append(rfd)
        bcmd += ["--ro-bind-data", str(rfd), "/tmp/cbuild-chroot-wrapper.sh"]

    if fakeroot:
        bcmd += [
            "--setenv", "FAKEROOTDONTTRYCHOWN", "1", "--", "sh",
            get_fakeroot(False)
        ]

    if wrapper:
        bcmd += ["sh", "/tmp/cbuild-chroot-wrapper.sh"]

    bcmd.append(cmd)
    bcmd += args

    try:
        return subprocess.run(
            bcmd, env = envs, capture_output = capture_output, check = check,
            stdout = stdout, stderr = stderr, pass_fds = tuple(fdlist)
        )
    finally:
        for fd in fdlist:
            os.close(fd)
