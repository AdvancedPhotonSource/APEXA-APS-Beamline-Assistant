"""Audit every FF candidate and sample raw support without selecting a grain count.

The 3-degree display components and raw-pixel signal checks are diagnostics,
not rules for merging candidates or declaring physical grains.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_run(layer):
    grain_path = layer / 'Grains.csv'
    columns = next(s for s in grain_path.read_text().splitlines()
                   if s.startswith('%GrainID')).lstrip('%').split()
    grains = np.atleast_2d(np.loadtxt(grain_path, comments='%'))
    spots = np.atleast_2d(np.loadtxt(layer / 'SpotMatrix.csv', comments='%'))
    raw = np.genfromtxt(layer / 'InputAllExtraInfoFittingAll.csv', names=True)
    rotations = Rotation.from_matrix(grains[:, 1:10].reshape(-1, 3, 3)).as_matrix()
    symmetry = Rotation.create_group('O').as_matrix()
    ids = grains[:, 0].astype(int)
    sets = [set(spots[(spots[:, 0] == i) & (spots[:, 12] == 1), 1].astype(int)) for i in ids]
    n = len(ids)
    angles, overlaps = np.zeros((n, n)), np.eye(n)
    pairs = []
    for i, j in itertools.combinations(range(n), 2):
        relative = rotations[i] @ symmetry @ rotations[j].T
        angle = np.rad2deg(np.arccos(np.clip((np.trace(relative, axis1=1, axis2=2)-1)/2, -1, 1))).min()
        shared = len(sets[i] & sets[j])
        fraction = shared / min(len(sets[i]), len(sets[j]))
        angles[i, j] = angles[j, i] = angle
        overlaps[i, j] = overlaps[j, i] = fraction
        pairs.append({'ids': [int(ids[i]), int(ids[j])], 'angle_deg': float(angle),
                      'shared_spots': shared, 'overlap_fraction_of_smaller': fraction})
    # Connected components are for the figure only, never a deduplicated result.
    labels = np.full(n, -1)
    for start in range(n):
        if labels[start] >= 0:
            continue
        label = int(labels.max()) + 1
        stack = [start]
        while stack:
            i = stack.pop()
            if labels[i] >= 0:
                continue
            labels[i] = label
            stack.extend(j for j in range(n) if labels[j] < 0 and angles[i, j] < 3.0)
    rows = []
    for i, g in enumerate(grains):
        others = set().union(*(s for j, s in enumerate(sets) if j != i))
        rows.append({'id': int(ids[i]), 'xyz_um': g[10:13].tolist(),
            'confidence': float(g[columns.index('Confidence')]),
            'diff_pos_post_um': float(g[columns.index('DiffPosPost')]),
            'matched_spots': len(sets[i]), 'unshared_spots': len(sets[i] - others),
            'orientation_display_component': int(labels[i])})
    result = {'candidate_count': n, 'matched_assignments': sum(map(len, sets)),
              'distinct_assigned_spots': len(set.union(*sets)), 'candidates': rows,
              'pairs': pairs, 'orientation_display_components': int(labels.max())+1,
              'display_rule': 'Connected components with cubic misorientation <3 degrees; no grain merging',
              'source_sha256': {p.name: sha(p) for p in [grain_path, layer/'SpotMatrix.csv', layer/'InputAllExtraInfoFittingAll.csv']}}
    return result, raw, sets, angles, overlaps


def sample_raw(raw_path, raw, sets, result, output, reuse_patches=None):
    import h5py
    observations, patches = [], []
    saved = np.load(reuse_patches)['patches'] if reuse_patches else None
    chosen = []
    lookup = {int(row['SpotID']): row for row in raw}
    for candidate, ids in zip(result['candidates'], sets):
        ordered = sorted(ids, key=lambda i: (lookup[i]['OmegaIni'], i))
        chosen.extend((candidate['id'], ordered[j]) for j in
                      np.unique(np.linspace(0, len(ordered)-1, min(8, len(ordered)), dtype=int)))
    with h5py.File(raw_path, 'r') as f:
        data = f['exchange/data']
        for grain, spot in chosen:
            p = lookup[spot]
            x, y = round(float(p['YRawPx'])), 2879-round(float(p['ZRawPx']))
            centre = round((float(p['OmegaIni'])-180)/-.2500031845)+1
            frames = [k for k in range(centre-1, centre+2) if 1 <= k < data.shape[0]]
            if not (10 <= x < data.shape[2]-10 and 10 <= y < data.shape[1]-10):
                raise ValueError('Raw sample falls near the detector edge; report rather than clip')
            patch = (saved[len(patches)] if saved is not None else
                np.max(np.stack([data[k, y-10:y+11, x-10:x+11] for k in frames]), axis=0).astype(float))
            yy, xx = np.mgrid[-10:11, -10:11]
            background = patch[(abs(xx) >= 7) | (abs(yy) >= 7)]
            signal = patch[7:14, 7:14]
            median = float(np.median(background))
            sigma = max(float(np.std(background)), 1.0)
            excess = float(signal.max()) - median
            row = {'grain_id': grain, 'spot_id': int(spot), 'raw_frames': frames,
                   'raw_row': y, 'raw_column': x, 'omega_ini_deg': float(p['OmegaIni']),
                   'central_max_counts': float(signal.max()), 'background_median_counts': median,
                   'background_sd_counts': sigma, 'excess_over_background_sd': excess/sigma,
                   'local_signal_present': bool(excess >= 20 and excess/sigma >= 5)}
            observations.append(row)
            patches.append(patch)
    np.savez_compressed(output/'raw-patches.npz', patches=np.stack(patches))
    if saved is not None and len(saved) != len(patches):
        raise ValueError('Saved raw patches do not match the fixed sample schedule')
    return {'selection': '8 evenly spaced OmegaIni ranks per candidate, selected before reading intensities',
            'mapping': 'raw column=YRawPx; raw row=2879-ZRawPx; maximum over a three-frame bracket',
            'signal_rule': 'central 7x7 maximum exceeds outer background median by >=20 counts and >=5 background SD',
            'limit': 'Supports detected signal near assigned omega; not independent orientation validation, held-out indexing or absolute omega zero',
            'observations': observations,
            'signal_present': sum(p['local_signal_present'] for p in observations),
            'samples': len(observations)}


def figure(result, angles, overlaps, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    rows = result['candidates']
    ids = [r['id'] for r in rows]
    order = sorted(range(len(rows)), key=lambda i: (rows[i]['orientation_display_component'], ids[i]))
    xyz = np.array([r['xyz_um'] for r in rows])
    fig, ax = plt.subplots(1, 3, figsize=(15, 4.8), constrained_layout=True)
    ax[0].scatter(xyz[:,0], xyz[:,1], c=[r['orientation_display_component'] for r in rows],
                  cmap='tab10', s=[25+50*r['confidence'] for r in rows], edgecolors='black', linewidths=.5)
    for r in rows:
        ax[0].annotate(str(r['id']), r['xyz_um'][:2], fontsize=6, xytext=(2,3), textcoords='offset points')
    ax[0].set(xlabel='Refined X (µm)', ylabel='Refined Y (µm)', title='All 19 candidates; no count filtering')
    ax[0].set_aspect('equal', adjustable='datalim')
    for a, matrix, title, label, maximum in [
        (ax[1],angles,'Cubic orientation differences','Degrees',62.8),
        (ax[2],overlaps,'Shared indexed spot IDs','Fraction of smaller spot set',1)]:
        im=a.imshow(matrix[np.ix_(order,order)],vmin=0,vmax=maximum,cmap='viridis')
        a.set(xticks=range(len(ids)),yticks=range(len(ids)),
              xticklabels=[ids[i] for i in order],yticklabels=[ids[i] for i in order],title=title)
        a.tick_params(axis='both',labelsize=6)
        plt.setp(a.get_xticklabels(),rotation=90)
        fig.colorbar(im,ax=a,shrink=.8,label=label)
    fig.savefig(output/'candidate-audit.pdf')
    fig.savefig(output/'candidate-audit.png',dpi=180)
    plt.close(fig)


def raw_figure(raw_support, output):
    import matplotlib.pyplot as plt
    patches = np.load(output/'raw-patches.npz')['patches']
    observations = raw_support['observations']
    ids = sorted({p['grain_id'] for p in observations})
    fig, axes = plt.subplots(4, 5, figsize=(12, 10), constrained_layout=True)
    for grain, ax in zip(ids, axes.flat):
        indices = [i for i, p in enumerate(observations) if p['grain_id'] == grain]
        i = indices[len(indices)//2]  # fixed median-omega sample, not brightest
        p = observations[i]
        ax.imshow(np.log1p(patches[i]), cmap='magma', origin='upper', extent=(-10.5,10.5,10.5,-10.5))
        ax.plot(0,0,'+',color='cyan',markersize=7)
        ax.set_title(f"Candidate {grain}; spot {p['spot_id']}\nraw frames {p['raw_frames'][0]}–{p['raw_frames'][-1]}", fontsize=9)
        ax.set(xticks=[-10,0,10],yticks=[-10,0,10])
        ax.tick_params(labelsize=7)
    for ax in list(axes.flat)[len(ids):]:
        ax.axis('off')
    fig.suptitle('Raw detector signal for every candidate: median-omega sample\n21×21 pixels; log(1+counts), per-panel scale; three-frame maximum',fontsize=12)
    fig.savefig(output/'raw-support.pdf')
    fig.savefig(output/'raw-support.png',dpi=160)
    plt.close(fig)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root',required=True,type=Path)
    ap.add_argument('--output',required=True,type=Path)
    ap.add_argument('--raw',type=Path)
    ap.add_argument('--reuse-raw-patches',type=Path)
    a=ap.parse_args()
    a.output.mkdir(parents=True,exist_ok=False)
    summary={'operator_hypothesis':2,'physical_grain_count':'unresolved','script_sha256':sha(Path(__file__)),'runs':{}}
    for tag in ['raw-clean','geometry-1']:
        values=read_run(a.root/'reconstruction'/tag/'LayerNr_1')
        summary['runs'][tag]=values[0]
    result,raw,sets,angles,overlaps=values
    if a.raw:
        summary['raw_support']=sample_raw(a.raw,raw,sets,result,a.output,a.reuse_raw_patches)
    figure(result,angles,overlaps,a.output)
    if a.raw:
        raw_figure(summary['raw_support'],a.output)
    (a.output/'candidate-audit.json').write_text(json.dumps(summary,indent=2)+'\n')
    columns=['id','orientation_display_component','confidence','matched_spots','unshared_spots','diff_pos_post_um']
    with (a.output/'candidates.csv').open('w') as f:
        f.write(','.join(columns+['x_um','y_um','z_um'])+'\n')
        for row in result['candidates']:
            f.write(','.join(str(row[k]) for k in columns)+','+','.join(map(str,row['xyz_um']))+'\n')
    print(json.dumps({'candidate_counts':{k:v['candidate_count'] for k,v in summary['runs'].items()},
                      'raw_samples':summary.get('raw_support',{}).get('samples')}))


if __name__=='__main__':
    main()
