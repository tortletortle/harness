//! 构建脚本：把 git 描述信息注入编译期环境变量，
//! 让 release 产物可以追溯到确切代码版本（docs/VERSIONING.md）。
use std::process::Command;

fn git(args: &[&str]) -> Option<String> {
    let out = Command::new("git").args(args).output().ok()?;
    if !out.status.success() {
        return None;
    }
    let s = String::from_utf8(out.stdout).ok()?;
    let s = s.trim().to_string();
    if s.is_empty() {
        None
    } else {
        Some(s)
    }
}

fn main() {
    // 形如 v0.2.0-3-gabc1234（若无 tag 则退化为短哈希），加 -dirty 后缀标记未提交改动。
    let describe = git(&["describe", "--tags", "--always", "--dirty"])
        .unwrap_or_else(|| "unknown".to_string());
    let commit_date = git(&["log", "-1", "--format=%cd", "--date=short"])
        .unwrap_or_else(|| "unknown".to_string());
    println!("cargo:rustc-env=EAS_GIT_DESCRIBE={describe}");
    println!("cargo:rustc-env=EAS_GIT_COMMIT_DATE={commit_date}");
    // 提交或打 tag 时重新运行：HEAD、分支 ref、tag 目录、packed-refs 任一变化都触发。
    println!("cargo:rerun-if-changed=../../.git/HEAD");
    println!("cargo:rerun-if-changed=../../.git/refs/heads");
    println!("cargo:rerun-if-changed=../../.git/refs/tags");
    println!("cargo:rerun-if-changed=../../.git/packed-refs");
}
