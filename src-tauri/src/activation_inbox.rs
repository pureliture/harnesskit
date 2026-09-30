use std::sync::{Arc, Mutex, Weak};

use crate::app_controller::AppController;

trait ActivationTarget: Send + Sync {
    fn deliver(&self);
}

impl ActivationTarget for AppController {
    fn deliver(&self) {
        if let Err(error) = self.enqueue_main_window_reactivation() {
            log::error!("main window reactivation enqueue failed: {}", error.code);
        }
    }
}

#[derive(Default)]
struct ActivationInboxState {
    pending: bool,
    target: Option<Weak<dyn ActivationTarget>>,
}

#[derive(Clone, Default)]
pub(crate) struct ActivationInbox {
    state: Arc<Mutex<ActivationInboxState>>,
}

impl ActivationInbox {
    pub(crate) fn request(&self) {
        let target = {
            let mut state = self
                .state
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            match state.target.as_ref().and_then(Weak::upgrade) {
                Some(target) => Some(target),
                None => {
                    state.target = None;
                    state.pending = true;
                    None
                }
            }
        };
        if let Some(target) = target {
            target.deliver();
        }
    }

    pub(crate) fn bind_controller(&self, controller: Arc<AppController>) {
        let target: Arc<dyn ActivationTarget> = controller;
        self.bind_target(&target);
    }

    fn bind_target(&self, target: &Arc<dyn ActivationTarget>) {
        let pending = {
            let mut state = self
                .state
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            state.target = Some(Arc::downgrade(target));
            std::mem::take(&mut state.pending)
        };
        if pending {
            target.deliver();
        }
    }
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::{Arc, Barrier};

    use super::{ActivationInbox, ActivationTarget};

    #[derive(Default)]
    struct CountingTarget {
        deliveries: AtomicUsize,
    }

    impl ActivationTarget for CountingTarget {
        fn deliver(&self) {
            self.deliveries.fetch_add(1, Ordering::SeqCst);
        }
    }

    fn erased(target: &Arc<CountingTarget>) -> Arc<dyn ActivationTarget> {
        target.clone()
    }

    #[test]
    fn pre_bind_requests_are_coalesced_and_drained_once() {
        let inbox = ActivationInbox::default();
        let target = Arc::new(CountingTarget::default());

        inbox.request();
        inbox.request();
        inbox.bind_target(&erased(&target));

        assert_eq!(target.deliveries.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn post_bind_requests_are_forwarded_individually() {
        let inbox = ActivationInbox::default();
        let target = Arc::new(CountingTarget::default());
        let erased = erased(&target);
        inbox.bind_target(&erased);

        inbox.request();
        inbox.request();

        assert_eq!(target.deliveries.load(Ordering::SeqCst), 2);
    }

    #[test]
    fn request_and_bind_race_delivers_exactly_once() {
        for _ in 0..64 {
            let inbox = ActivationInbox::default();
            let target = Arc::new(CountingTarget::default());
            let erased = erased(&target);
            let barrier = Arc::new(Barrier::new(2));
            let request_inbox = inbox.clone();
            let request_barrier = Arc::clone(&barrier);
            let requester = std::thread::spawn(move || {
                request_barrier.wait();
                request_inbox.request();
            });

            barrier.wait();
            inbox.bind_target(&erased);
            requester.join().unwrap();

            assert_eq!(target.deliveries.load(Ordering::SeqCst), 1);
        }
    }
}
