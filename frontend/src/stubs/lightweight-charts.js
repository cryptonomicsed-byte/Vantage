export function createChart() {
  return {
    addCandlestickSeries() { return { setData() {}, update() {} } },
    addLineSeries() { return { setData() {}, update() {} } },
    addHistogramSeries() { return { setData() {}, update() {} } },
    applyOptions() {},
    resize() {},
    remove() {},
    timeScale() { return { fitContent() {}, scrollToRealTime() {} } },
    subscribeCrosshairMove() {},
    unsubscribeCrosshairMove() {},
  }
}
export const ColorType = { Solid: 'solid', VerticalGradient: 'gradient' }
export const CrosshairMode = { Normal: 0, Magnet: 1 }
export const LineStyle = { Solid: 0, Dotted: 1, Dashed: 2 }
export const PriceScaleMode = { Normal: 0, Logarithmic: 1, Percentage: 2, IndexedTo100: 3 }
