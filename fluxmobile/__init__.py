from .fluxmobile import FluxMobile


async def setup(bot):
    await bot.add_cog(FluxMobile(bot))
