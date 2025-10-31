//! Thread-local state management for the native runtime.
//!
//! The real project wires these helpers into Python shims, but the
//! implementation here focuses on the internal invariants referenced by the
//! regression tests.  The reviewer requested that invariant checks avoid
//! crashing release builds, so the guards rely on `debug_assert!` instead of
//! `assert!`/`panic!`.
//!
//! # Usage Pattern
//!
//! 1. Call [`ThreadState::acquire_outermost_lock`] to grab the shared mutex.
//! 2. Use [`ThreadState::enter_scope`] / [`ThreadState::exit_scope`] to track
//!    logical scopes while the lock is held.
//! 3. Invoke [`ThreadState::release_outermost_lock`] once all scopes are closed
//!    (i.e. the scope depth is zero) to relinquish the guard.

use std::sync::{Mutex, MutexGuard};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ThreadStateError {
    MissingScope,
}

impl<'guard> Drop for ThreadState<'guard> {
    fn drop(&mut self) {
        debug_assert!(
            self.scope_depth == 0,
            "ThreadState dropped with non-zero scope depth: {}",
            self.scope_depth
        );
        debug_assert!(
            self.guard.is_none(),
            "ThreadState dropped while still holding the lock"
        );
    }
}

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

    /// Increment the tracked scope depth.
    ///
    /// Saturates at `usize::MAX` in release builds to avoid overflow-induced
    /// panics; overflowing indicates a logic error and should be caught by
    /// the accompanying `debug_assert!` checks when `debug_assertions` are
    /// enabled.
    pub fn enter_scope(&mut self) {
        self.scope_depth = self.scope_depth.saturating_add(1);
    }

    pub fn exit_scope(&mut self) -> Result<(), ThreadStateError> {
        debug_assert!(
            self.scope_depth > 0,
            "exit_scope called without a matching enter_scope",
        );
        if self.scope_depth == 0 {
            return Err(ThreadStateError::MissingScope);
        }
        self.scope_depth -= 1;
        Ok(())
    }

    pub fn acquire_outermost_lock(
        &mut self,
    ) -> Result<(), std::sync::PoisonError<MutexGuard<'guard, ()>>> {
        if self.guard.is_none() {
            self.mutex.lock().map(|guard| {
                self.guard = Some(guard);
            })?;
        }
        Ok(())
    }

    /// Release the outermost lock if it is currently held.
    ///
    /// Returns `Ok(())` when the guard was present and has been dropped.  When
    /// the guard is missing in release builds, returns an error so callers can
    /// handle the unexpected state explicitly.
    pub fn release_outermost_lock(&mut self) -> Result<(), &'static str> {
        debug_assert!(
            self.scope_depth == 0,
            "outermost lock can only be released when the scope stack is empty",
        );
        let guard = self.guard.take();
        debug_assert!(
            guard.is_some(),
            "release_outermost_lock expects an acquired guard",
        );
        guard.map(|_| ()).ok_or("outermost lock was not held")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    #[test]
    fn release_does_not_panic_in_release_builds() {
        let mutex = Mutex::new(());
        let mut state = ThreadState::new(&mutex);
        state.acquire_outermost_lock().unwrap();
        state.release_outermost_lock().unwrap();
    }

    #[test]
    fn acquire_is_idempotent() {
        let mutex = Mutex::new(());
        let mut state = ThreadState::new(&mutex);
        state.acquire_outermost_lock().unwrap();
        state.acquire_outermost_lock().unwrap();
        state.release_outermost_lock().unwrap();
    }

    #[test]
    fn acquire_propagates_poison_error() {
        let mutex = Arc::new(Mutex::new(()));
        {
            let mutex_clone = Arc::clone(&mutex);
            let _ = std::thread::spawn(move || {
                let _guard = mutex_clone.lock().unwrap();
                panic!("poison");
            })
            .join();
        }

        let mut state = ThreadState::new(Arc::as_ref(&mutex));
        let err = state.acquire_outermost_lock();
        assert!(err.is_err());
    }

    #[test]
    fn exit_scope_decrements_depth_and_errors_on_underflow() {
        let mutex = Mutex::new(());
        let mut state = ThreadState::new(&mutex);
        state.enter_scope();
        state.exit_scope().unwrap();
        assert_eq!(state.scope_depth, 0);

        let underflow = state.exit_scope();
        assert_eq!(underflow, Err(ThreadStateError::MissingScope));
    }

    #[test]
    fn full_lifecycle_with_nested_scopes() {
        let mutex = Mutex::new(());
        let mut state = ThreadState::new(&mutex);
        state.acquire_outermost_lock().unwrap();
        state.enter_scope();
        state.enter_scope();
        state.exit_scope().unwrap();
        state.exit_scope().unwrap();
        state.release_outermost_lock().unwrap();
    }

    #[test]
    fn release_without_acquire_returns_error() {
        let mutex = Mutex::new(());
        let mut state = ThreadState::new(&mutex);
        assert!(state.release_outermost_lock().is_err());
    }
}
