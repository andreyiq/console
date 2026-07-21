// build.rs — линковка libmario.a (nesrecomp C runtime) для RISC-V target.
use std::path::PathBuf;

fn main() {
  let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
  let nesrecomp_dir = manifest.join("src/nesrecomp");
  let lib = nesrecomp_dir.join("libmario.a");

  if !lib.exists() {
    println!("cargo:warning=libmario.a не найден в {}. Запустите build_libmario.sh", nesrecomp_dir.display());
    std::process::exit(1);
  }

  println!("cargo:rustc-link-search=native={}", nesrecomp_dir.display());
  println!("cargo:rustc-link-lib=static=mario");
  println!("cargo:rerun-if-changed={}", lib.display());
}
