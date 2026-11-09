"""Exact frame selection for the native calibration runner.

MIDAS remains responsible for reading, sentinel handling and image transforms.
An HDF5 virtual dataset selects a bounded frame interval without copying raw data
or converting integer sentinels to floating-point counts before MIDAS sees them.
"""
from contextlib import contextmanager
from pathlib import Path
import tempfile


@contextmanager
def selected_source(path, *, data_loc, skip_frame=0, frame_count=0, frame_reduce='median'):
    if skip_frame < 0 or frame_count < 0:
        raise ValueError('skip_frame and frame_count must be nonnegative')
    if frame_reduce not in ('mean', 'median'):
        raise ValueError('frame_reduce must be mean or median')
    source = Path(path).resolve()
    record = {'source': str(source), 'data_loc': data_loc, 'skip_frame': skip_frame,
              'frame_count_requested': frame_count, 'frame_reduce': frame_reduce}
    if source.suffix.lower() not in ('.h5', '.hdf5', '.hdf', '.nxs'):
        if frame_count:
            raise ValueError('A bounded frame_count currently requires HDF5 input')
        record['frame_indices'] = None
        yield str(source), skip_frame, record
        return
    import h5py
    with h5py.File(source, 'r') as raw:
        data = raw[data_loc]
        shape, dtype = data.shape, data.dtype
        if len(shape) not in (2, 3):
            raise ValueError(f'Expected a detector image or frame stack, got {shape}')
        total = shape[0] if len(shape) == 3 else 1
        stop = skip_frame + frame_count if frame_count else total
        if skip_frame >= total or stop > total:
            raise ValueError(f'Requested frames [{skip_frame}, {stop}) exceed {total} frames')
        record.update(source_shape=list(shape), source_dtype=str(dtype),
                      frame_indices=list(range(skip_frame, stop)), selected_frames=stop-skip_frame)
    # The upstream reader already implements unbounded skip + reduction.
    if not frame_count or len(shape) == 2:
        yield str(source), skip_frame, record
        return
    with tempfile.TemporaryDirectory(prefix='apexa_calibration_frames_') as scratch:
        selected = Path(scratch) / 'selected.h5'
        layout = h5py.VirtualLayout(shape=(stop-skip_frame, *shape[1:]), dtype=dtype)
        layout[:] = h5py.VirtualSource(str(source), data_loc, shape=shape)[skip_frame:stop]
        with h5py.File(selected, 'w', libver='latest') as view:
            view.create_virtual_dataset(data_loc, layout)
        yield str(selected), 0, record
