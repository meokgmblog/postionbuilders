ce_df = None
            for _, row in ce_opts.iterrows():
                opt_data = filter_market_hours(get_derivative_intraday(ACCESS_TOKEN, row[key_col]))
                if not opt_data.empty:
                    opt_sub = opt_data[["timestamp", "oi"]].copy()
                    if ce_df is None:
                        ce_df = opt_sub.rename(columns={"oi": "ce_oi"})
                    else:
                        ce_df = pd.merge(ce_df, opt_sub, on="timestamp", how="outer")
                        ce_df["ce_oi"] = ce_df["ce_oi"].fillna(0) + ce_df["oi"].fillna(0)
                        ce_df.drop(columns=["oi"], inplace=True)

            pe_df = None
            for _, row in pe_opts.iterrows():
                opt_data = filter_market_hours(get_derivative_intraday(ACCESS_TOKEN, row[key_col]))
                if not opt_data.empty:
                    opt_sub = opt_data[["timestamp", "oi"]].copy()
                    if pe_df is None:
                        pe_df = opt_sub.rename(columns={"oi": "pe_oi"})
                    else:
                        pe_df = pd.merge(pe_df, opt_sub, on="timestamp", how="outer")
                        pe_df["pe_oi"] = pe_df["pe_oi"].fillna(0) + pe_df["oi"].fillna(0)
                        pe_df.drop(columns=["oi"], inplace=True)
