/** Generated from ontology/coast-raid-k/gis.json. Do not edit. */
export const GIS = {
  "package": "coast-raid-k",
  "frame": {
    "origin": "岸线中点 O",
    "x": "东距，东为正",
    "y": "高，上为正",
    "z": "距岸，陆正海负",
    "heading000_deg": 0,
    "heading000": "向陆（+z）",
    "heading180": "向海（−z）"
  },
  "shoreline": {
    "id": "SHORE_0",
    "type": "Shoreline",
    "kind": "harmonic",
    "plane": "y=0",
    "note": "Divides SEA/LAND. Does not translate envelopes or C_low.",
    "params": {
      "terms": [
        {
          "amp": 0.14,
          "freq": 0.62,
          "phase": 0
        },
        {
          "amp": 0.055,
          "freq": 1.28,
          "phase": 0.8
        },
        {
          "amp": 0.02,
          "freq": 2.7,
          "phase": 1.4
        }
      ]
    }
  },
  "regions": [
    {
      "id": "SEA",
      "type": "Region",
      "predicate": "z < shoreline(x)"
    },
    {
      "id": "LAND",
      "type": "Region",
      "predicate": "z >= shoreline(x)"
    }
  ],
  "membership": [
    {
      "of": "C_low",
      "region": "SEA",
      "rule": "subset"
    },
    {
      "of": "B_ship",
      "region": "SEA",
      "rule": "subset"
    },
    {
      "of": "JAM_BLOOM",
      "region": "SEA",
      "rule": "subset"
    },
    {
      "of": "B_BMD",
      "region": "LAND",
      "rule": "spans"
    },
    {
      "of": "B_SAM",
      "region": "LAND",
      "rule": "spans"
    },
    {
      "of": "SEARCH",
      "region": "LAND",
      "rule": "spans"
    },
    {
      "of": "SHIP",
      "region": "SEA",
      "rule": "in"
    },
    {
      "of": "JAMMER",
      "region": "SEA",
      "rule": "in"
    },
    {
      "of": "C2",
      "region": "LAND",
      "rule": "in"
    },
    {
      "of": "RADAR_SHORE",
      "region": "LAND",
      "rule": "in"
    },
    {
      "of": "RADAR_SAM",
      "region": "LAND",
      "rule": "in"
    },
    {
      "of": "SAM_TEL",
      "region": "LAND",
      "rule": "in"
    },
    {
      "of": "BMD_SILO",
      "region": "LAND",
      "rule": "in"
    }
  ],
  "grid": {
    "step_km": 1,
    "seaGrid": false,
    "landGrid": "faint"
  },
  "presentation": {
    "kind": "sandTable",
    "excludes": [
      "satellite",
      "sky",
      "trees",
      "clouds"
    ]
  },
  "crs": {
    "id": "CRK:ENG",
    "type": "EngineeringCRS",
    "unit": "http://qudt.org/vocab/unit/KiloM",
    "epsg": null,
    "note": "Local sand-table. x east, y up, z shore-distance. 1 = 1 km. Not EPSG geographic. Not a real harbour."
  }
};

export function shoreZ(x) {
  const terms = GIS.shoreline.params.terms;
  let z = 0;
  for (const t of terms) z += t.amp * Math.sin(x * t.freq + t.phase);
  return z;
}

export function inSea(p) {
  return p[2] < shoreZ(p[0]);
}

export function inLand(p) {
  return p[2] >= shoreZ(p[0]);
}

export function regionOf(p) {
  return inSea(p) ? "SEA" : "LAND";
}
