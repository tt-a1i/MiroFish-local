export const BUILD_INFO = {
  gitSha: import.meta.env.VITE_GIT_SHA || 'dev',
  buildTime: import.meta.env.VITE_BUILD_TIME || 'local'
}
