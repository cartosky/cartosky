export type Rgba = readonly [number, number, number, number];
export type FixtureCorner = "topLeft" | "topRight" | "bottomLeft" | "bottomRight";

export type StillExportFixture = {
  id: string;
  manifest: {
    model: string;
    run: string;
    variable: { key: string; label: string };
    region: { id: string; label: string };
    fh: number;
  };
  frame: {
    width: number;
    height: number;
    fill: Rgba;
    markers: ReadonlyArray<{
      corner: FixtureCorner;
      x: number;
      y: number;
      rgba: Rgba;
    }>;
  };
  output: {
    width: number;
    height: number;
    expectedCorners: Readonly<Record<FixtureCorner, Rgba>>;
  };
};

const CROP_MARKERS = {
  topLeft: [241, 17, 31, 255],
  topRight: [37, 211, 71, 255],
  bottomLeft: [43, 89, 233, 255],
  bottomRight: [251, 197, 23, 255],
} as const satisfies Record<FixtureCorner, Rgba>;

export const STILL_EXPORT_FIXTURES: ReadonlyArray<StillExportFixture> = [
  {
    id: "mrms-reflectivity-conus-horizontal-crop",
    manifest: {
      model: "MRMS",
      run: "20260728_1800",
      variable: { key: "reflectivity", label: "Composite Reflectivity" },
      region: { id: "conus", label: "CONUS" },
      fh: 0,
    },
    frame: {
      width: 8,
      height: 4,
      fill: [9, 23, 37, 255],
      markers: [
        { corner: "topLeft", x: 2, y: 0, rgba: CROP_MARKERS.topLeft },
        { corner: "topRight", x: 5, y: 0, rgba: CROP_MARKERS.topRight },
        { corner: "bottomLeft", x: 2, y: 3, rgba: CROP_MARKERS.bottomLeft },
        { corner: "bottomRight", x: 5, y: 3, rgba: CROP_MARKERS.bottomRight },
      ],
    },
    output: {
      width: 4,
      height: 4,
      expectedCorners: CROP_MARKERS,
    },
  },
  {
    id: "hrrr-ptype-conus-vertical-crop",
    manifest: {
      model: "HRRR",
      run: "20260728_18z",
      variable: { key: "radar_ptype", label: "Precipitation Type & Intensity" },
      region: { id: "conus", label: "CONUS" },
      fh: 12,
    },
    frame: {
      width: 4,
      height: 8,
      fill: [13, 29, 43, 255],
      markers: [
        { corner: "topLeft", x: 0, y: 2, rgba: CROP_MARKERS.topLeft },
        { corner: "topRight", x: 3, y: 2, rgba: CROP_MARKERS.topRight },
        { corner: "bottomLeft", x: 0, y: 5, rgba: CROP_MARKERS.bottomLeft },
        { corner: "bottomRight", x: 3, y: 5, rgba: CROP_MARKERS.bottomRight },
      ],
    },
    output: {
      width: 4,
      height: 4,
      expectedCorners: CROP_MARKERS,
    },
  },
  {
    id: "gfs-tmp2m-conus-no-crop",
    manifest: {
      model: "GFS",
      run: "20260728_12z",
      variable: { key: "tmp2m", label: "Surface Temperature" },
      region: { id: "conus", label: "CONUS" },
      fh: 24,
    },
    frame: {
      width: 4,
      height: 4,
      fill: [17, 31, 47, 255],
      markers: [
        { corner: "topLeft", x: 0, y: 0, rgba: CROP_MARKERS.topLeft },
        { corner: "topRight", x: 3, y: 0, rgba: CROP_MARKERS.topRight },
        { corner: "bottomLeft", x: 0, y: 3, rgba: CROP_MARKERS.bottomLeft },
        { corner: "bottomRight", x: 3, y: 3, rgba: CROP_MARKERS.bottomRight },
      ],
    },
    output: {
      width: 4,
      height: 4,
      expectedCorners: CROP_MARKERS,
    },
  },
];

export const GIF_EXPORT_FIXTURE = {
  id: "gfs-tmp2m-conus-two-frame-gif",
  width: 6,
  height: 4,
  delayMs: 120,
  frames: [
    {
      left: [21, 96, 189, 255],
      right: [225, 74, 51, 255],
    },
    {
      left: [225, 74, 51, 255],
      right: [21, 96, 189, 255],
    },
  ] as const,
  expectedFrameCount: 2,
  expectedSha256: "7b1905564f560b8cbdcfcbd7b3fe565cd2a2ea70645e010c7dcb4aa55979685b",
} as const;
