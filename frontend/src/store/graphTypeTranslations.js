import { getGraphTypeTranslations } from '../api/graph'
import { setGraphTypeTranslations } from '../utils/entityTranslations.js'

let loadingPromise = null
let loaded = false
let loadError = null

export const loadGraphTypeTranslations = async ({ force = false } = {}) => {
  if (loaded && !force) return { loaded: true, error: null }
  if (loadingPromise && !force) return loadingPromise

  loadingPromise = getGraphTypeTranslations()
    .then(res => {
      if (res.success && res.data) {
        setGraphTypeTranslations(res.data)
        loaded = true
        loadError = null
      }
      return { loaded, error: loadError }
    })
    .catch(error => {
      loadError = error
      return { loaded: false, error }
    })
    .finally(() => {
      loadingPromise = null
    })

  return loadingPromise
}

export const isGraphTypeTranslationsLoaded = () => loaded

export const getGraphTypeTranslationsLoadError = () => loadError
