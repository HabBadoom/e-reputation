"""Scanner → AnyPulse integration layer.

Scanner reaches AnyPulse only through this client and the signed `/integrations/scanner/*`
endpoints, even though both run in the same process today: the contract stays the same if
Scanner is deployed separately later.
"""
