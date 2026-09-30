use sha2::{Digest, Sha256};
use subtle::ConstantTimeEq;

pub(crate) struct ContentDigest;

pub(crate) struct IncrementalContentDigest(Sha256);

impl ContentDigest {
    pub(crate) fn sha256(bytes: &[u8]) -> [u8; 32] {
        Sha256::digest(bytes).into()
    }

    pub(crate) fn incremental() -> IncrementalContentDigest {
        IncrementalContentDigest(Sha256::new())
    }

    pub(crate) fn constant_time_eq(left: &[u8; 32], right: &[u8; 32]) -> bool {
        bool::from(left.ct_eq(right))
    }

    pub(crate) fn hex(digest: &[u8; 32]) -> String {
        digest.iter().map(|byte| format!("{byte:02x}")).collect()
    }
}

impl IncrementalContentDigest {
    pub(crate) fn update(&mut self, bytes: &[u8]) {
        self.0.update(bytes);
    }

    pub(crate) fn finalize(self) -> [u8; 32] {
        self.0.finalize().into()
    }
}
