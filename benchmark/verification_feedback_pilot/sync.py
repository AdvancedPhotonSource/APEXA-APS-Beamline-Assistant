#!/usr/bin/env python3
"""Refresh only small study records in the single existing Box mirror."""
from pathlib import Path
import subprocess


MIRROR = Path('/Users/b324240/Library/CloudStorage/Box-Box/01-Research/01-APEXA/'
              'ai_tune_rot_series/harness_runs/r5_local_audit/guided_followup/'
              'ff_alleppey/verification_feedback_pilot')


def sync():
    MIRROR.mkdir(parents=True,exist_ok=True)
    args=['rsync','-az','-e','ssh -T -o RemoteCommand=none -o BatchMode=yes -o ConnectTimeout=12']
    for pattern in ('source/','runtime/','public/','work/','__pycache__/','*.h5','*.tif','*.png','*.npy','*.npz'):
        args.append('--exclude='+pattern)
    for pattern in ('*/','*.json','*.jsonl','*.log','*.txt','*.md','*.py','*.csv','*.params'):
        args.append('--include='+pattern)
    args += ['--exclude=*','alleppey:/scratch/ptripathi/apexa-cvpr/verification_feedback_pilot/',str(MIRROR)+'/']
    subprocess.run(args,check=True)  # no --delete: retain local endpoint receipts


if __name__=='__main__':sync()
