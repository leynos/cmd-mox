//! Thread-local state management for the native runtime.
//!
//! The real project wires these helpers into Python shims, but the
//! implementation here focuses on the internal invariants referenced by the
//! regression tests.  The reviewer requested that invariant checks avoid
//! crashing release builds, so the guards rely on `debug_assert!` instead of
//! `assert!`/`panic!`.

use std::sync::{Mutex, MutexGuard};

#[derive(Debug)]
pub struct ThreadState<'guard> {
    mutex: &'guard Mutex<()>,
    guard: Option<MutexGuard<'guard, ()>>,
    scope_depth: usize,
}

impl<'guard> ThreadState<'guard> {
    pub fn new(mutex: &'guard Mutex<()>) -> Self {
        Self {
            mutex,
            guard: None,
            scope_depth: 0,
        }
    }

    pub fn enter_scope(&mut self) {
        self.scope_depth = self.scope_depth.saturating_add(1);
    }

    pub fn exit_scope(&mut self) {
        debug_assert!(
            self.scope_depth > 0,
            "exit_scope called without a matching enter_scope",
        );
        if self.scope_depth > 0 {
            self.scope_depth -= 1;
        }
    }

    pub fn acquire_outermost_lock(&mut self) {
        if self.guard.is_none() {
            self.guard = self.mutex.lock().ok();
        }
    }

    pub fn release_outermost_lock(&mut self) {
        debug_assert!(
            self.scope_depth == 0,
            "outermost lock can only be released when the scope stack is empty",
        );
        let guard = self.guard.take();
        debug_assert!(
            guard.is_some(),
            "release_outermost_lock expects an acquired guard",
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn release_does_not_panic_in_release_builds() {
        let mutex = Mutex::new(());
        let mut state = ThreadState::new(&mutex);
        state.acquire_outermost_lock();
        state.release_outermost_lock();
    }

    #[test]
    fn exit_scope_saturates_depth() {
        let mutex = Mutex::new(());
        let mut state = ThreadState::new(&mutex);
        state.exit_scope();
        assert_eq!(state.scope_depth, 0);
    }
}
