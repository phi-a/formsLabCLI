"""Plotly 3D spacecraft scene with animated orbit-environment vectors."""

import numpy as np
import plotly.graph_objects as go

from .surfaces import RealizedGeometry, rect_patch_grid


# ── Mesh construction ─────────────────────────────────────────────────────────

def _quad_arrays(surface, nx=None, ny=None):
    """Vertex coords and triangle indices for a patch-resolved surface."""
    if nx is None or ny is None:
        nx, ny = surface.patch_shape or (1, 1)
    dx = surface.width / nx
    dy = surface.height / ny
    u_ax = surface.u_axis
    v_ax = surface.v_axis
    xx, yy = rect_patch_grid(surface.width, surface.height, nx, ny)

    # Winding: default (0,1,2)+(0,2,3) yields normal = u_ax × v_ax.
    # Flip if that opposes the surface's outward normal.
    flip = np.dot(np.cross(u_ax, v_ax), surface.normal) < 0

    n = ny * nx
    verts = np.empty((n * 4, 3))
    ti = np.empty(n * 2, dtype=int)
    tj = np.empty(n * 2, dtype=int)
    tk = np.empty(n * 2, dtype=int)

    vi, ci = 0, 0
    for j in range(ny):
        for i in range(nx):
            cx, cy = float(xx[j, i]), float(yy[j, i])
            for sv, su in [(-1, -1), (1, -1), (1, 1), (-1, 1)]:
                verts[vi] = (
                    surface.center
                    + (cx + su * dx / 2) * u_ax
                    + (cy + sv * dy / 2) * v_ax
                )
                vi += 1
            b = vi - 4
            if flip:
                ti[ci], tj[ci], tk[ci] = b, b + 2, b + 1
                ti[ci + 1], tj[ci + 1], tk[ci + 1] = b, b + 3, b + 2
            else:
                ti[ci], tj[ci], tk[ci] = b, b + 1, b + 2
                ti[ci + 1], tj[ci + 1], tk[ci + 1] = b, b + 2, b + 3
            ci += 2

    return verts, ti, tj, tk


def _block_mean(arr, ny_new, nx_new):
    """Block-average last two axes of arr to (ny_new, nx_new)."""
    ny, nx = arr.shape[-2], arr.shape[-1]
    by, bx = max(1, ny // ny_new), max(1, nx // nx_new)
    yt, xt = ny_new * by, nx_new * bx
    sliced = arr[..., :yt, :xt]
    return sliced.reshape(*arr.shape[:-2], ny_new, by, nx_new, bx).mean(axis=(-3, -1))


def _fit_data(arr, ny, nx):
    """Resample a 2-D array to (ny, nx) via nearest-neighbour if needed."""
    arr = np.asarray(arr, dtype=float)
    if arr.shape == (ny, nx):
        return arr
    src_ny, src_nx = arr.shape
    iy = np.clip((np.arange(ny) * src_ny / ny).astype(int), 0, src_ny - 1)
    ix = np.clip((np.arange(nx) * src_nx / nx).astype(int), 0, src_nx - 1)
    return arr[np.ix_(iy, ix)]


def mesh(surface, *, data=None, color='#C0C0C0', opacity=0.85,
         cmin=None, cmax=None, colorscale='Plasma',
         showscale=False, cbar_title='', name=None):
    """Build a ``go.Mesh3d`` for one rectangular surface."""
    verts, ti, tj, tk = _quad_arrays(surface)
    kw = dict(
        x=verts[:, 0], y=verts[:, 1], z=verts[:, 2],
        i=ti, j=tj, k=tk,
        opacity=opacity,
        flatshading=True,
        name=name or surface.name,
        showlegend=False,
    )
    if data is not None:
        nx, ny = surface.patch_shape or (1, 1)
        arr = _fit_data(data, ny, nx)
        kw.update(
            intensity=np.repeat(arr.ravel(), 2),
            intensitymode='cell',
            colorscale=colorscale,
            cmin=cmin, cmax=cmax,
            showscale=showscale,
            colorbar=dict(title=cbar_title, len=0.55) if showscale else None,
            lighting=dict(ambient=1.0, diffuse=0.0, specular=0.0,
                          roughness=0.0, fresnel=0.0),
        )
    else:
        kw['color'] = color
    return go.Mesh3d(**kw)


def edges(surface, *, color='rgba(255,255,255,0.3)', width=1):
    """Build a ``go.Scatter3d`` for the patch grid lines of one surface."""
    nx, ny = surface.patch_shape or (1, 1)
    u_ax, v_ax = surface.u_axis, surface.v_axis
    hw, hh = 0.5 * surface.width, 0.5 * surface.height
    xs = np.linspace(-hw, hw, nx + 1)
    ys = np.linspace(-hh, hh, ny + 1)
    c = surface.center

    nan3 = np.array([np.nan, np.nan, np.nan])
    pts = []
    for xi in xs:
        pts += [c + xi * u_ax + ys[0] * v_ax,
                c + xi * u_ax + ys[-1] * v_ax, nan3]
    for yi in ys:
        pts += [c + xs[0] * u_ax + yi * v_ax,
                c + xs[-1] * u_ax + yi * v_ax, nan3]

    p = np.array(pts)
    return go.Scatter3d(
        x=p[:, 0], y=p[:, 1], z=p[:, 2],
        mode='lines', line=dict(color=color, width=width),
        showlegend=False, hoverinfo='skip',
    )


# ── Environment vectors ──────────────────────────────────────────────────────

def _arrow(direction, length, color, label, origin=None):
    """Shaft + cone head + text label for a directional arrow."""
    origin = np.zeros(3) if origin is None else np.asarray(origin, dtype=float)
    d = np.asarray(direction, dtype=float)
    n = np.linalg.norm(d)
    d = d / n if n > 1e-15 else np.array([0.0, 0.0, 1.0])
    tip = origin + d * length

    shaft = go.Scatter3d(
        x=[origin[0], tip[0]], y=[origin[1], tip[1]], z=[origin[2], tip[2]],
        mode='lines', line=dict(color=color, width=5),
        showlegend=False, hoverinfo='skip',
    )
    head = go.Cone(
        x=[tip[0]], y=[tip[1]], z=[tip[2]],
        u=[d[0]], v=[d[1]], w=[d[2]],
        sizemode='absolute', sizeref=length * 0.07,
        colorscale=[[0, color], [1, color]],
        showscale=False, showlegend=False, hoverinfo='skip',
    )
    off = tip + d * length * 0.12
    txt = go.Scatter3d(
        x=[off[0]], y=[off[1]], z=[off[2]],
        mode='text', text=[label],
        textfont=dict(color=color, size=13),
        showlegend=False, hoverinfo='skip',
    )
    return [shaft, head, txt]


def _body_axes(scale):
    """Body-frame axis indicators (+X red, +Y green, +Z blue)."""
    traces = []
    for lbl, vec, col in [
        ('+X', [1, 0, 0], '#e74c3c'),
        ('+Y', [0, 1, 0], '#2ecc71'),
        ('+Z', [0, 0, 1], '#3498db'),
    ]:
        traces.extend(_arrow(vec, scale, col, lbl))
    return traces


# ── Earth sphere ──────────────────────────────────────────────────────────────

def _earth_mesh_arrays(n_lon=24, n_lat=12):
    """Unit-radius UV sphere mesh for go.Mesh3d Earth rendering.

    Returns (verts, ti, tj, tk) where verts is (n_verts, 3) on the unit sphere.
    """
    verts = [[0.0, 0.0, -1.0]]  # south pole
    for lat_i in range(n_lat):
        lat = -np.pi / 2 + (lat_i + 1) * np.pi / (n_lat + 1)
        for lon_i in range(n_lon):
            lon = lon_i * 2 * np.pi / n_lon
            verts.append([
                np.cos(lat) * np.cos(lon),
                np.cos(lat) * np.sin(lon),
                np.sin(lat),
            ])
    verts.append([0.0, 0.0, 1.0])  # north pole
    verts = np.array(verts, dtype=float)

    n_v = len(verts)
    s_pole, n_pole = 0, n_v - 1
    ti, tj, tk = [], [], []

    # South cap
    for i in range(n_lon):
        ti.append(s_pole); tj.append(1 + i); tk.append(1 + (i + 1) % n_lon)
    # Mid quads
    for row in range(n_lat - 1):
        for col in range(n_lon):
            v0 = 1 + row * n_lon + col
            v1 = 1 + row * n_lon + (col + 1) % n_lon
            v2 = 1 + (row + 1) * n_lon + col
            v3 = 1 + (row + 1) * n_lon + (col + 1) % n_lon
            ti += [v0, v0]; tj += [v1, v3]; tk += [v3, v2]
    # North cap
    for i in range(n_lon):
        ti.append(n_pole)
        tj.append(1 + (n_lat - 1) * n_lon + (i + 1) % n_lon)
        tk.append(1 + (n_lat - 1) * n_lon + i)

    return (verts,
            np.array(ti, dtype=int), np.array(tj, dtype=int), np.array(tk, dtype=int))


def _earth_trace(verts, ti, tj, tk, radius, center=None):
    """Build a go.Mesh3d Earth sphere from pre-computed unit-sphere arrays."""
    c = np.zeros(3) if center is None else np.asarray(center, dtype=float)
    v = radius * verts + c
    return go.Mesh3d(
        x=v[:, 0], y=v[:, 1], z=v[:, 2],
        i=ti, j=tj, k=tk,
        color='#1a5c8a',
        showscale=False, showlegend=False, hoverinfo='skip',
        flatshading=False,
        lighting=dict(ambient=0.4, diffuse=0.9, specular=0.3, roughness=0.6),
        lightposition=dict(x=3, y=2, z=4),
    )


# ── Orbit helpers ────────────────────────────────────────────────────────────

def orbit_vectors(orbit, law, u):
    """Sun and Earth directions in body frame at each orbit sample.

    Returns (sun_body, earth_body), each shaped (n, 3).
    """
    u = np.asarray(u, dtype=float)
    sun_eci = orbit.sun_eci()
    n = u.size
    sun_body = np.zeros((n, 3))
    earth_body = np.zeros((n, 3))
    for k in range(n):
        R = law(u[k], orbit)
        sun_body[k] = R.m.T @ sun_eci
        earth_body[k] = R.m.T @ orbit.nadir_eci(u[k])
    return sun_body, earth_body


def eci_to_lvlh(orbit, u, vec_eci):
    """Convert a constant ECI unit vector to the LVLH frame at each orbit sample.

    Parameters
    ----------
    orbit : Orbit
    u : array-like [n]
    vec_eci : array-like (3,)

    Returns
    -------
    ndarray [n, 3]
    """
    u = np.asarray(u, dtype=float)
    vec = np.asarray(vec_eci, dtype=float)
    n = u.size
    result = np.empty((n, 3))
    for k in range(n):
        result[k] = orbit.eci_from_lvlh(u[k]).T @ vec
    return result


# ── Layout ────────────────────────────────────────────────────────────────────

_BG = '#000000'


def _layout(title=''):
    return go.Layout(
        scene=dict(
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
            zaxis=dict(visible=False),
            aspectmode='data',
            bgcolor=_BG,
            camera=dict(
                eye=dict(x=1.4, y=-1.6, z=1.0),
                up=dict(x=0, y=0, z=1),
            ),
        ),
        paper_bgcolor=_BG,
        font=dict(color='white', family='monospace'),
        margin=dict(l=0, r=0, t=50, b=10),
        title=dict(text=title, font=dict(size=14)),
    )


def _layout_orbit(title=''):
    """Initial layout for orbit-mode; camera is overridden per frame."""
    return go.Layout(
        scene=dict(
            xaxis=dict(visible=False),
            yaxis=dict(visible=False),
            zaxis=dict(visible=False),
            aspectmode='data',
            bgcolor=_BG,
            camera=dict(
                eye=dict(x=1.4, y=-0.5, z=0.4),
                up=dict(x=0, y=0, z=1),
            ),
        ),
        paper_bgcolor=_BG,
        font=dict(color='white', family='monospace'),
        margin=dict(l=0, r=0, t=50, b=10),
        title=dict(text=title, font=dict(size=14)),
    )


def _animation_controls(u):
    """Slider + play/pause buttons for an animated figure."""
    steps = [
        dict(args=[[str(k)], dict(mode='immediate',
                                  frame=dict(duration=0, redraw=True))],
             label=f'{np.degrees(u[k]):.0f}', method='animate')
        for k in range(u.size)
    ]
    return dict(
        sliders=[dict(
            active=0, steps=steps,
            currentvalue=dict(prefix='u = ', suffix='\u00b0'),
            x=0.08, len=0.84, font=dict(size=10),
        )],
        updatemenus=[dict(
            type='buttons', showactive=False, x=0.02, y=0.02,
            buttons=[
                dict(label='\u25b6', method='animate',
                     args=[None, dict(frame=dict(duration=80, redraw=True),
                                      fromcurrent=True, mode='immediate')]),
                dict(label='\u23f8', method='animate',
                     args=[[None], dict(frame=dict(duration=0, redraw=True),
                                        mode='immediate')]),
            ],
        )],
    )


# ── Public API ────────────────────────────────────────────────────────────────

def _tag_color(surface):
    if 'solar_panel' in surface.tags:
        return '#5B8FF9'
    if 'bus' in surface.tags:
        return '#888888'
    return '#666666'


def _extent(realized):
    corners = np.concatenate([s.corners() for s in realized.facets], axis=0)
    return float(np.ptp(corners, axis=0).max())


def scene(realized, *, data=None, label='', colorscale='Plasma',
          cmin=None, cmax=None,
          sun=None, earth=None, target=None, title='Spacecraft geometry'):
    """Static 3D overview of the realized spacecraft geometry."""
    data = data or {}
    if data and (cmin is None or cmax is None):
        all_vals = np.concatenate([np.asarray(v).ravel() for v in data.values()])
        if cmin is None: cmin = float(all_vals.min())
        if cmax is None: cmax = float(all_vals.max())

    traces = []
    first_data_surf = True
    for s in realized.facets:
        if 'solar_panel_back' in s.tags:
            continue
        if s.name in data:
            traces.append(mesh(
                s, data=data[s.name],
                cmin=cmin, cmax=cmax, colorscale=colorscale,
                showscale=first_data_surf, cbar_title=label, opacity=1.0,
            ))
            first_data_surf = False
        else:
            traces.append(mesh(s, color=_tag_color(s), opacity=0.8))
        if s.patch_shape is not None:
            traces.append(edges(s))

    ext = _extent(realized)
    traces.extend(_body_axes(ext * 0.45))
    if sun is not None:
        traces.extend(_arrow(sun, ext * 0.6, '#f1c40f', 'Sun'))
    if earth is not None:
        traces.extend(_arrow(earth, ext * 0.6, '#00bcd4', 'Earth'))
    if target is not None:
        traces.extend(_arrow(target, ext * 0.6, '#9B8FE8', 'Target'))

    return go.Figure(data=traces, layout=_layout(title))


# ── Layer extraction helpers ──────────────────────────────────────────────────

def vf_layer(profiles, field):
    """Extract one VF field → animate-ready dict[name, ndarray[n_time,ny,nx]]."""
    if isinstance(profiles, dict):
        items = profiles.values()
    else:
        items = profiles
    return {p.name: np.asarray(getattr(p, field)) for p in items}


def tenv_layer(env_profiles):
    """Extract Tenv → animate-ready dict[name, ndarray[n_time,ny,nx]]."""
    if hasattr(env_profiles, 'name'):
        return {env_profiles.name: np.asarray(env_profiles.Tenv)}
    if isinstance(env_profiles, dict):
        items = env_profiles.values()
    else:
        items = env_profiles
    return {p.name: np.asarray(p.Tenv) for p in items}


def thermal_layer(thermal_profiles):
    """Extract temperature → animate-ready dict[name, ndarray[n_time,ny,nx]]."""
    if hasattr(thermal_profiles, 'name'):
        return {thermal_profiles.name: np.asarray(thermal_profiles.temperature)}
    if isinstance(thermal_profiles, dict):
        items = thermal_profiles.values()
    else:
        items = thermal_profiles
    return {p.name: np.asarray(p.temperature) for p in items}


def animate_layers(realized, layers, *, sun=None, earth=None, u, eclipse,
                   labels=None, colorscales=None, title=''):
    """Produce one animated figure per layer."""
    labels = labels or {}
    colorscales = colorscales or {}
    figs = {}
    for name, data in layers.items():
        fig = animate(
            realized, data,
            sun=sun, earth=earth,
            u=u, eclipse=eclipse,
            label=labels.get(name, name),
            colorscale=colorscales.get(name, 'Plasma'),
            title=f'{title}  [{name}]' if title else name,
        )
        figs[name] = fig
    return figs


def animate(realized, data, *, sun=None, earth=None, target=None,
            orbit=None, law=None,
            u, eclipse, label='', colorscale='Plasma', title=''):
    """Animated 3D scene with per-patch data evolution and environment vectors.

    **Body-frame mode** (default — no ``orbit``/``law``): geometry fixed at
    origin, thermal intensity evolves, sun/earth/target arrows rotate in body
    frame as the orbit progresses.

    **Camera-fixed mode** (``orbit`` and ``law`` supplied): spacecraft sits at
    origin with camera locked to it.  Earth sphere orbits at the correct angular
    scale (radius/distance ≈ 0.934 for 450 km LEO) so Earth fills the background
    correctly as the attitude law rotates the nadir direction.

    Parameters
    ----------
    realized : RealizedGeometry
    data : dict[str, ndarray [n_time, ny, nx]]
    sun : ndarray [n_time, 3], optional
        Body-frame sun direction for body-frame mode (ignored in camera-fixed mode).
    earth : ndarray [n_time, 3], optional
        Body-frame nadir arrow for body-frame mode (ignored in camera-fixed mode).
    target : array-like (3,) ECI or ndarray [n_time, 3] body-frame, optional
        Target direction.  In camera-fixed mode a shape-(3,) ECI vector is
        converted to body frame at each sample.  Drawn as a purple arrow.
    orbit : Orbit, optional
        Enables camera-fixed mode when provided alongside ``law``.
    law : attitude law callable, optional
        Enables camera-fixed mode when provided alongside ``orbit``.
    u : ndarray [n_time]
    eclipse : ndarray [n_time] bool
    label, colorscale, title : str
    """
    u = np.asarray(u, dtype=float)
    eclipse = np.asarray(eclipse, dtype=bool)
    n_time = u.size

    all_vals = np.concatenate([v.ravel() for v in data.values()])
    cmin, cmax = float(all_vals.min()), float(all_vals.max())

    ext = _extent(realized)
    arrow_len = ext * 0.7

    # ── Orbit mode: spacecraft orbits Earth, camera tracks spacecraft ─────
    if orbit is not None and law is not None:
        # Orbit-plane frame (fixed ECI frame aligned to orbit)
        h_hat  = orbit.h_hat_eci
        r0_hat = orbit.r_hat_eci(0.0)
        v0_hat = np.cross(h_hat, r0_hat)
        R_eci_to_orbit = np.array([r0_hat, v0_hat, h_hat])  # rows = orbit axes in ECI

        # Body → orbit-plane rotation at each sample
        R_all = np.empty((n_time, 3, 3))
        for k in range(n_time):
            R_all[k] = R_eci_to_orbit @ law(float(u[k]), orbit).m

        # Display scale: Earth at origin, SC orbits at orbit_r_d
        orbit_r_d = ext * 6.0
        earth_r_d = ext * 3.0        # artistic scale — full sphere clearly visible
        arrow_len = ext * 1.2
        half_range = orbit_r_d       # Plotly normalises camera by this scale

        # Spacecraft position in orbit-plane frame
        sc_pos = orbit_r_d * np.column_stack(
            [np.cos(u), np.sin(u), np.zeros(n_time)]
        )

        # Constant directions in orbit-plane frame
        sun_orbit = R_eci_to_orbit @ orbit.sun_eci()
        target_orbit = None
        if target is not None:
            tgt = np.asarray(target, dtype=float)
            if tgt.ndim == 1:
                target_orbit = R_eci_to_orbit @ tgt

        # Camera offset in body frame: behind (−X = anti-velocity) and above (+Z = zenith)
        cam_body = np.array([0.0, -ext * 1.2, ext * 1.2])

        # Earth sphere — fixed at orbit centre
        e_verts, e_ti, e_tj, e_tk = _earth_mesh_arrays()
        fixed = [_earth_trace(e_verts, e_ti, e_tj, e_tk, earth_r_d)]
        # Faint orbit ring for context
        ring_u_arr = np.linspace(0, 2 * np.pi, 200)
        fixed.append(go.Scatter3d(
            x=orbit_r_d * np.cos(ring_u_arr),
            y=orbit_r_d * np.sin(ring_u_arr),
            z=np.zeros(200),
            mode='lines', line=dict(color='rgba(255,255,255,0.12)', width=1),
            showlegend=False, hoverinfo='skip',
        ))
        n_fixed = len(fixed)

        # Reduced-resolution geometry for orbit animation
        lvlh_nx, lvlh_ny = 8, 6
        displayable = [s for s in realized.facets
                       if 'solar_panel_back' not in s.tags]
        geo = {
            s.name: (_quad_arrays(s, lvlh_nx, lvlh_ny) if s.name in data
                     else _quad_arrays(s, 1, 1))
            for s in displayable
        }
        intensities_l = {
            s.name: np.repeat(
                _block_mean(np.asarray(data[s.name]), lvlh_ny, lvlh_nx
                            ).reshape(n_time, -1),
                2, axis=1,
            )
            for s in displayable if s.name in data
        }

        # Initial animated traces (frame 0)
        R0, r0 = R_all[0], sc_pos[0]
        anim0 = []
        first_data = True
        for s in displayable:
            v, ti, tj, tk = geo[s.name]
            vr = (R0 @ v.T).T + r0
            kw = dict(x=vr[:, 0], y=vr[:, 1], z=vr[:, 2],
                      i=ti, j=tj, k=tk,
                      flatshading=True, showlegend=False, name=s.name)
            if s.name in data:
                kw.update(
                    intensity=intensities_l[s.name][0],
                    intensitymode='cell',
                    colorscale=colorscale, cmin=cmin, cmax=cmax,
                    showscale=first_data,
                    colorbar=dict(title=label, len=0.55) if first_data else None,
                    opacity=1.0,
                    lighting=dict(ambient=1.0, diffuse=0.0, specular=0.0,
                                  roughness=0.0, fresnel=0.0),
                )
                first_data = False
            else:
                kw.update(color=_tag_color(s), opacity=0.7)
            anim0.append(go.Mesh3d(**kw))
        anim0.extend(_arrow(sun_orbit, arrow_len, '#f1c40f', 'Sun', origin=r0))
        if target_orbit is not None:
            anim0.extend(_arrow(target_orbit, arrow_len, '#9b59b6', 'Target', origin=r0))

        n_anim   = len(anim0)
        anim_idx = list(range(n_fixed, n_fixed + n_anim))

        def _cam_layout(k):
            """Per-frame layout that locks the camera to the spacecraft."""
            R, r = R_all[k], sc_pos[k]
            eye_d = r + R @ cam_body           # camera position in orbit-plane frame
            eye_n = eye_d / half_range         # Plotly normalised coordinates
            ctr_n = r   / half_range           # look-at = spacecraft centre
            up_v  = R @ np.array([0.0, 0.0, 1.0])   # body +Z = zenith
            return dict(scene=dict(camera=dict(
                eye=dict(x=float(eye_n[0]), y=float(eye_n[1]), z=float(eye_n[2])),
                center=dict(x=float(ctr_n[0]), y=float(ctr_n[1]), z=float(ctr_n[2])),
                up=dict(x=float(up_v[0]),  y=float(up_v[1]),  z=float(up_v[2])),
            )))

        # Animation frames
        frames = []
        for k in range(n_time):
            R, r = R_all[k], sc_pos[k]
            fd = []
            for s in displayable:
                v, ti, tj, tk = geo[s.name]
                vr = (R @ v.T).T + r
                if s.name in data:
                    fd.append(go.Mesh3d(x=vr[:, 0], y=vr[:, 1], z=vr[:, 2],
                                        i=ti, j=tj, k=tk,
                                        intensity=intensities_l[s.name][k]))
                else:
                    fd.append(go.Mesh3d(x=vr[:, 0], y=vr[:, 1], z=vr[:, 2],
                                        i=ti, j=tj, k=tk))
            fd.extend(_arrow(sun_orbit, arrow_len, '#f1c40f', 'Sun', origin=r))
            if target_orbit is not None:
                fd.extend(_arrow(target_orbit, arrow_len, '#9b59b6', 'Target', origin=r))
            frames.append(go.Frame(
                data=fd, traces=anim_idx, layout=_cam_layout(k), name=str(k)
            ))

        fig = go.Figure(data=fixed + anim0, frames=frames, layout=_layout_orbit(title))
        fig.update_layout(**_animation_controls(u))
        return fig

    # ── Body-frame mode ────────────────────────────────────────────────────
    fixed = []
    for s in realized.facets:
        if 'solar_panel_back' in s.tags:
            continue
        if s.name in data:
            continue
        fixed.append(mesh(s, color=_tag_color(s), opacity=0.55))

    fixed.extend(_body_axes(ext * 0.45))
    n_fixed = len(fixed)

    ordered = [realized.by_name(nm) for nm in data]
    anim0 = []
    for idx, s in enumerate(ordered):
        anim0.append(mesh(
            s, data=data[s.name][0],
            cmin=cmin, cmax=cmax, colorscale=colorscale,
            showscale=(idx == 0), cbar_title=label, opacity=1.0,
        ))
    if sun is not None:
        anim0.extend(_arrow(sun[0], arrow_len, '#f1c40f', 'Sun'))
    if earth is not None:
        anim0.extend(_arrow(earth[0], arrow_len, '#00bcd4', 'Earth'))
    if target is not None:
        t0 = np.asarray(target)
        anim0.extend(_arrow(t0[0] if t0.ndim > 1 else t0, arrow_len, '#9b59b6', 'Target'))
    n_anim = len(anim0)
    anim_idx = list(range(n_fixed, n_fixed + n_anim))

    intensities = {
        s.name: np.repeat(data[s.name].reshape(n_time, -1), 2, axis=1)
        for s in ordered
    }

    frames = []
    for k in range(n_time):
        fd = []
        for s in ordered:
            fd.append(go.Mesh3d(intensity=intensities[s.name][k]))
        if sun is not None:
            fd.extend(_arrow(sun[k], arrow_len, '#f1c40f', 'Sun'))
        if earth is not None:
            fd.extend(_arrow(earth[k], arrow_len, '#00bcd4', 'Earth'))
        if target is not None:
            t = np.asarray(target)
            fd.extend(_arrow(t[k] if t.ndim > 1 else t, arrow_len, '#9b59b6', 'Target'))
        frames.append(go.Frame(data=fd, traces=anim_idx, name=str(k)))

    fig = go.Figure(data=fixed + anim0, frames=frames, layout=_layout(title))
    fig.update_layout(**_animation_controls(u))
    return fig
