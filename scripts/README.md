# Shared research modules

The live collection entry point is section 28 of the root hormuz_textminig_v2.ipynb (settings cell 59, execution cell 60). body_pilot_200.py supplies shared automatic access, storage and recovery primitives; its CLI reads status only. candidate_selection_rules.py and select_distributed_candidates.py reproduce existing metadata classification. prepare_body_pilot_200.py reproduces the historical fixed sample without creating approval files. Existing offline regression tests are retained; do not execute historical crawlers as import checks.
