from . import dupes, junk, messages, people, receipts, screenshots, selfies

ALL_SCAN_DETECTORS = [
    screenshots.detect,
    selfies.detect,
    receipts.detect,
    messages.detect,
    dupes.detect,
    junk.detect,
    people.detect,
]
